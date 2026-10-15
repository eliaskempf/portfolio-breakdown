"""Release checks use invented metadata, clocks and temporary user state."""
import json
from threading import Event

import pytest

from portfolio_app.updates import (DAY, WEEK, RETRY, Release, UpdateService, UpdateStore,
    UpdateStateError, parse_release, version_number)


def metadata(version='0.2.0', target='linux-x64'):
    return {'tag_name': 'v' + version, 'draft': False, 'prerelease': False,
            'html_url': 'https://untrusted.invalid',
            'assets': [{'name': f'portfolio-breakdown-{version}-{target}.tar.gz', 'state': 'uploaded'}]}


def completed(service, **kwargs):
    assert service.schedule(**kwargs)
    service._thread.join(timeout=3)
    assert not service.pending()
    return service.store.read()


def test_numeric_versions_and_known_release_link():
    assert version_number('0.10.0') > version_number('0.9.9')
    release = parse_release(metadata(), 'linux-x64')
    assert release.url == 'https://github.com/eliaskempf/portfolio-breakdown/releases/tag/v0.2.0'
    assert release.version == '0.2.0'


@pytest.mark.parametrize('version', ['v1.0.0-rc1', '1.0.0.dev1', '1.0', '01.0.0', '../../path', None, 2])
def test_invalid_or_prerelease_versions_are_not_updates(version):
    assert version_number(version) is None


@pytest.mark.parametrize('field,value', [('draft', True), ('prerelease', True), ('tag_name', 'v0.2.0rc1')])
def test_nonstable_releases_are_ignored(field, value):
    raw = metadata()
    raw[field] = value
    assert parse_release(raw, 'linux-x64') is None


@pytest.mark.parametrize('target,asset', [
    ('windows-x64', 'portfolio-breakdown-0.2.0-windows-x64-setup.exe'),
    ('windows-x64', 'portfolio-breakdown-0.2.0-windows-x64.zip'),
    ('linux-x64', 'portfolio-breakdown-experimental_0.2.0_amd64.deb'),
    ('macos-arm64', 'portfolio-breakdown-experimental-0.2.0-macos-arm64.dmg'),
    ('source', 'portfolio_breakdown-0.2.0.tar.gz'),
])
def test_platform_assets_must_be_uploaded_and_match_version(target, asset):
    raw = metadata()
    raw['assets'] = [{'name': asset, 'state': 'uploaded'}]
    assert parse_release(raw, target) == Release('v0.2.0')
    raw['assets'][0]['state'] = 'new'
    assert parse_release(raw, target) is None
    assert parse_release(metadata(), 'macos-arm64') is None
    assert parse_release(metadata(), 'unsupported') is None


def test_daily_checks_version_comparison_and_restart(tmp_path):
    clock = [1000000.]
    fetched = []
    def fetch(target):
        fetched.append(target)
        return Release('v0.2.0')
    service = UpdateService(tmp_path, 'linux-x64', fetch=fetch, now=lambda: clock[0])
    raw = completed(service)
    assert service.available(raw, '0.1.0') == Release('v0.2.0')
    assert service.available(raw, '0.2.0') is None
    assert service.available(raw, '0.3.0') is None
    assert service.available(raw, '0.3.0.dev1') is None
    restarted = UpdateService(tmp_path, 'linux-x64', fetch=fetch, now=lambda: clock[0])
    assert not restarted.schedule()
    assert not restarted.schedule(force=True)
    clock[0] += DAY
    completed(restarted)
    assert fetched == ['linux-x64', 'linux-x64']


def test_failure_retains_cache_backoff_and_suppresses_dialog(tmp_path):
    clock = [1000000.]
    service = UpdateService(tmp_path, 'linux-x64', fetch=lambda _: Release('v0.2.0'), now=lambda: clock[0])
    first = completed(service)
    clock[0] += DAY
    def fail(_):
        raise OSError('Invented outage')
    service.fetch = fail
    failed = completed(service)
    assert failed['checks']['linux-x64']['checked'] == first['checks']['linux-x64']['checked']
    assert failed['checks']['linux-x64']['failed']
    assert service.available(failed, '0.1.0')
    assert not service.claim_reminder('0.1.0')
    assert not service.schedule()
    clock[0] += RETRY
    service.fetch = lambda _: None
    recovered = completed(service)
    assert not service.available(recovered, '0.1.0')


def test_optout_during_download_survives_completion_and_manual_check(tmp_path):
    started, finish = Event(), Event()
    clock = [1000000.]
    def fetch(_):
        started.set()
        assert finish.wait(3)
        return Release('v0.2.0')
    service = UpdateService(tmp_path, 'linux-x64', fetch=fetch, now=lambda: clock[0])
    assert service.schedule()
    assert started.wait(2)
    assert not service.schedule(force=True)
    other = UpdateService(tmp_path, 'linux-x64', fetch=fetch, now=lambda: clock[0])
    assert not other.schedule()
    service.store.enable(False)
    finish.set()
    service._thread.join(3)
    assert service.store.read()['enabled'] is False
    assert not service.claim_reminder('0.1.0')
    clock[0] += DAY
    assert not other.schedule()
    completed(other, force=True)
    assert service.store.read()['enabled'] is False


def test_weekly_reminders_are_claimed_across_windows_and_versions(tmp_path):
    clock = [1000000.]
    service = UpdateService(tmp_path, 'linux-x64', fetch=lambda _: Release('v0.2.0'), now=lambda: clock[0])
    completed(service)
    assert service.claim_reminder('0.1.0')
    assert service.store.read()['remind_after'] == clock[0] + WEEK
    other = UpdateService(tmp_path, 'linux-x64', fetch=service.fetch, now=lambda: clock[0])
    assert not other.claim_reminder('0.1.0')
    clock[0] += DAY
    service.fetch = lambda _: Release('v0.3.0')
    completed(service)
    assert not service.claim_reminder('0.1.0')
    clock[0] += WEEK - DAY
    assert not service.claim_reminder('0.1.0')  # stale metadata cannot trigger a popup
    completed(service)
    assert service.claim_reminder('0.1.0')


def test_source_manual_default_and_cache_platform_separation(tmp_path):
    source = UpdateService(tmp_path, 'source', automatic=False, fetch=lambda _: Release('v0.2.0'))
    assert not source.schedule()
    completed(source, force=True)
    assert not source.claim_reminder('0.1.0')
    native = UpdateService(tmp_path, 'linux-x64', fetch=lambda _: None)
    assert native.available(native.store.read(), '0.1.0') is None
    completed(native)
    assert source.available(source.store.read(), '0.1.0')


@pytest.mark.parametrize('content', ['bad json', 'null', '{}', '{"version": 2}',
    '{"version":1,"enabled":false,"seen":"","remind_after":NaN,"checks":{}}'])
def test_bad_preferences_fail_closed_without_overwriting(tmp_path, content):
    path = tmp_path / 'updates.json'
    path.write_text(content)
    store = UpdateStore(tmp_path)
    with pytest.raises(UpdateStateError):
        store.read()
    service = UpdateService(tmp_path, 'source', fetch=lambda _: pytest.fail('No request permitted'))
    assert not service.schedule(force=True)
    assert path.read_text() == content


def test_atomic_save_failure_preserves_optout(tmp_path, monkeypatch):
    from pathlib import Path
    store = UpdateStore(tmp_path)
    store.enable(False)
    original = store.path.read_bytes()
    def fail(*args):
        raise OSError('Invented full disk')
    monkeypatch.setattr(Path, 'replace', fail)
    with pytest.raises(OSError):
        store.enable(True)
    assert store.path.read_bytes() == original
    assert not list(tmp_path.glob('*.tmp'))


def test_http_request_has_no_portfolio_or_credentials_and_is_bounded(monkeypatch):
    from portfolio_app import updates
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, limit):
            assert limit == updates.MAX_RESPONSE + 1
            return json.dumps(metadata()).encode()
    def open_request(request, timeout):
        assert request.full_url == updates.API_URL
        assert request.data is None
        assert request.get_header('Authorization') is None
        assert timeout == 6
        return Response()
    monkeypatch.setattr(updates, 'urlopen', open_request)
    assert updates.fetch_release('linux-x64') == Release('v0.2.0')


def test_platform_detection_and_source_default(monkeypatch):
    from portfolio_app import updates
    assert updates.source_checkout()
    assert updates.release_target() == 'source'
    monkeypatch.setattr(updates.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(updates.sys, 'platform', 'darwin')
    monkeypatch.setattr(updates.platform, 'machine', lambda: 'arm64')
    assert not updates.source_checkout()
    assert updates.release_target() == 'macos-arm64'
    monkeypatch.setattr(updates.platform, 'machine', lambda: 'x86_64')
    assert updates.release_target() == 'unsupported'
