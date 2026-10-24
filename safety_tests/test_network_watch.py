"""Watch lifecycle regression; all subprocesses and snapshots mocked."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('deploy_network_watch',ROOT/'ci/deploy.py')
deploy=importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class NetworkWatchTests(unittest.TestCase):
    def test_watch_cleanup_preserves_original_helm_failure(self):
        processes=[]
        for label in ('endpoints','pods'):
            process=Mock()
            process.stdout=iter([label+' ready\n'])
            process.poll.return_value=None
            processes.append(process)
        with tempfile.TemporaryDirectory() as folder,patch.object(deploy,'ROOT',Path(folder)), \
             patch.object(deploy,'network_snapshot') as snapshot, \
             patch.object(deploy.subprocess,'Popen',side_effect=processes) as popen:
            with self.assertRaisesRegex(RuntimeError,'original Helm failure'):
                with deploy.network_watch('dev'):
                    raise RuntimeError('original Helm failure')
            self.assertEqual([call.args[1] for call in snapshot.call_args_list],['before-upgrade','after-test'])
            self.assertEqual(popen.call_count,2)
            for process in processes:
                process.terminate.assert_called_once()
            captures=list(Path(folder).glob('evidence/network-dev-*/*-watch.txt'))
            self.assertEqual(len(captures),2)
            self.assertTrue(all('ready' in path.read_text() for path in captures))


if __name__=='__main__':unittest.main()
