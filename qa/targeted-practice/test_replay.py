import contextlib,io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import replay
class SafeTests(unittest.TestCase):
 def test_dryrun_no_subprocess_or_mutation(self):
  with patch.object(replay,'execute',side_effect=AssertionError),patch.object(replay.subprocess,'run',side_effect=AssertionError),contextlib.redirect_stdout(io.StringIO()) as out:
   self.assertEqual(replay.main(['ansible','--root','/not-used','--report','/not-used']),0)
  self.assertTrue(json.loads(out.getvalue())['dry_run'])
 def test_gate(self):
  with tempfile.TemporaryDirectory() as t:
   env=dict(GITHUB_ACTIONS='true',RUNNER_ENVIRONMENT='github-hosted',GITHUB_REF=replay.BRANCH,GITHUB_WORKSPACE=t)
   replay.gate(t,'ansible','targeted-ansible',env,'Linux')
   for bad in [dict(env,DOCKER_HOST='tcp://remote'),dict(env,GITHUB_REF='refs/heads/main'),dict(env,RUNNER_ENVIRONMENT='self-hosted')]:
    with self.assertRaises(ValueError):replay.gate(t,'ansible','targeted-ansible',bad,'Linux')
   with self.assertRaises(ValueError):replay.gate(t,'ansible','wrong',env,'Linux')
   (Path(t)/'.runtime').mkdir()
   with self.assertRaises(ValueError):replay.gate(t,'ansible','targeted-ansible',env,'Linux')
if __name__=='__main__':unittest.main()
