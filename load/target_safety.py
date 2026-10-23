"""Restrict the supplied load profiles to the bound kind API ports."""
from urllib.parse import urlsplit
import argparse
import os


def training_host(value):
    try:
        url = urlsplit(value or '')
        valid = (url.scheme == 'http' and url.hostname == '127.0.0.1'
                 and url.port in {18220, 18230} and not url.username and not url.password
                 and url.path in {'', '/'} and not url.query and not url.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('Load target must be http://127.0.0.1:18220 or :18230')
    return value.rstrip('/')


def check_user(user):
    # Check the actual session URL as well as the resolved CLI/env/UI host.
    expected = training_host(user.environment.host or user.host)
    if training_host(user.client.base_url) != expected:
        raise ValueError('Load session target differs from configured target')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', '-H', default=os.environ.get('LOCUST_HOST'))
    args, _ = parser.parse_known_args()
    try:
        training_host(args.host)
    except ValueError as error:
        parser.error(str(error))
