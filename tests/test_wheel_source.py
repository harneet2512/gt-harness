import zipfile
from pathlib import Path

import pytest

from scripts.verify_wheel_source import verify_wheel_source


def fixture(tmp_path: Path, members: dict[str, bytes]):
    wheel = tmp_path / 'fixture.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    source = tmp_path / 'src'
    (source / 'groundtruth').mkdir(parents=True)
    return wheel, source


def test_exact_source_includes_resources_and_detects_changed_bytes(tmp_path):
    wheel, source = fixture(tmp_path, {'groundtruth/a.py': b'x=1\n', 'groundtruth/a.sql': b'SELECT 1;'})
    (source / 'groundtruth/a.py').write_bytes(b'x=1\n')
    (source / 'groundtruth/a.sql').write_bytes(b'SELECT 1;')
    assert verify_wheel_source(wheel, source)['status'] == 'PASS'
    (source / 'groundtruth/a.py').write_bytes(b'x=2\n')
    result = verify_wheel_source(wheel, source)
    assert result['status'] == 'FAIL'
    assert result['changed'] == ['groundtruth/a.py']


def test_missing_and_extra_source_are_not_certified(tmp_path):
    wheel, source = fixture(tmp_path, {'groundtruth/runtime/gateway.py': b'pass\n'})
    (source / 'groundtruth/extra.py').write_bytes(b'pass\n')
    result = verify_wheel_source(wheel, source)
    assert result['status'] == 'FAIL'
    assert result['missing'] == ['groundtruth/runtime/gateway.py']
    assert result['extra'] == ['groundtruth/extra.py']


@pytest.mark.parametrize('name', ['groundtruth/../escape.py', 'groundtruth//a.py'])
def test_unsafe_member_rejected(tmp_path, name):
    wheel, source = fixture(tmp_path, {name: b'pass'})
    with pytest.raises(ValueError, match='unsafe_member'):
        verify_wheel_source(wheel, source)


def test_empty_package_fails(tmp_path):
    wheel, source = fixture(tmp_path, {'metadata.txt': b'x'})
    assert verify_wheel_source(wheel, source)['status'] == 'FAIL'


def test_line_endings_are_not_silently_normalized(tmp_path):
    wheel, source = fixture(tmp_path, {'groundtruth/a.py': b'x=1\r\n'})
    (source / 'groundtruth/a.py').write_bytes(b'x=1\n')
    assert verify_wheel_source(wheel, source)['status'] == 'FAIL'
