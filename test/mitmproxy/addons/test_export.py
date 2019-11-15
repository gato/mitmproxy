import os
import shlex

import pytest
import pyperclip

from mitmproxy import exceptions
from mitmproxy.addons import export  # heh
from mitmproxy.test import tflow
from mitmproxy.test import tutils
from mitmproxy.test import taddons
from unittest import mock


@pytest.fixture
def get_request():
    return tflow.tflow(
        req=tutils.treq(method=b'GET', content=b'', path=b"/path?a=foo&a=bar&b=baz"))


@pytest.fixture
def post_request():
    return tflow.tflow(
        req=tutils.treq(method=b'POST', headers=(), content=bytes(range(256))))


@pytest.fixture
def patch_request():
    return tflow.tflow(
        req=tutils.treq(
            method=b'PATCH',
            content=b'content',
            path=b"/path?query=param"
        )
    )


@pytest.fixture
def tcp_flow():
    return tflow.ttcpflow()


class TestExportCurlCommand:
    def test_get(self, get_request):
        result = """curl -H header:qvalue 'http://address:22/path?a=foo&a=bar&b=baz'"""
        assert export.curl_command(get_request) == result

    def test_post(self, post_request):
        post_request.request.content = b'nobinarysupport'
        result = "curl -H content-length:15 -X POST http://address:22/path --data-binary nobinarysupport"
        assert export.curl_command(post_request) == result

    def test_fails_with_binary_data(self, post_request):
        # shlex.quote doesn't support a bytes object
        # see https://github.com/python/cpython/pull/10871
        with pytest.raises(exceptions.CommandError):
            export.curl_command(post_request)

    def test_patch(self, patch_request):
        result = """curl -H header:qvalue -H content-length:7 -X PATCH 'http://address:22/path?query=param' --data-binary content"""
        assert export.curl_command(patch_request) == result

    def test_tcp(self, tcp_flow):
        with pytest.raises(exceptions.CommandError):
            export.curl_command(tcp_flow)

    def test_escape_single_quotes_in_body(self):
        request = tflow.tflow(
            req=tutils.treq(
                method=b'POST',
                headers=(),
                content=b"'&#"
            )
        )
        command = export.curl_command(request)
        assert shlex.split(command)[-2] == '--data-binary'
        assert shlex.split(command)[-1] == "'&#"


class TestExportHttpieCommand:
    def test_get(self, get_request):
        result = """http GET 'http://address:22/path?a=foo&a=bar&b=baz' header:qvalue"""
        assert export.httpie_command(get_request) == result

    def test_post(self, post_request):
        post_request.request.content = b'nobinarysupport'
        result = "http POST http://address:22/path content-length:15 <<< nobinarysupport"
        assert export.httpie_command(post_request) == result

    def test_fails_with_binary_data(self, post_request):
        # shlex.quote doesn't support a bytes object
        # see https://github.com/python/cpython/pull/10871
        with pytest.raises(exceptions.CommandError):
            export.httpie_command(post_request)

    def test_patch(self, patch_request):
        result = """http PATCH 'http://address:22/path?query=param' header:qvalue content-length:7 <<< content"""
        assert export.httpie_command(patch_request) == result

    def test_tcp(self, tcp_flow):
        with pytest.raises(exceptions.CommandError):
            export.httpie_command(tcp_flow)

    def test_escape_single_quotes_in_body(self):
        request = tflow.tflow(
            req=tutils.treq(
                method=b'POST',
                headers=(),
                content=b"'&#"
            )
        )
        command = export.httpie_command(request)
        assert shlex.split(command)[-2] == '<<<'
        assert shlex.split(command)[-1] == "'&#"


class TestRaw:
    def test_get(self, get_request):
        assert b"header: qvalue" in export.raw(get_request)

    def test_tcp(self, tcp_flow):
        with pytest.raises(exceptions.CommandError):
            export.raw(tcp_flow)


def qr(f):
    with open(f, "rb") as fp:
        return fp.read()


def test_export(tmpdir):
    f = str(tmpdir.join("path"))
    e = export.Export()
    with taddons.context():
        assert e.formats() == ["curl", "httpie", "raw"]
        with pytest.raises(exceptions.CommandError):
            e.file("nonexistent", tflow.tflow(resp=True), f)

        e.file("raw", tflow.tflow(resp=True), f)
        assert qr(f)
        os.unlink(f)

        e.file("curl", tflow.tflow(resp=True), f)
        assert qr(f)
        os.unlink(f)

        e.file("httpie", tflow.tflow(resp=True), f)
        assert qr(f)
        os.unlink(f)


@pytest.mark.parametrize("exception, log_message", [
    (PermissionError, "Permission denied"),
    (IsADirectoryError, "Is a directory"),
    (FileNotFoundError, "No such file or directory")
])
@pytest.mark.asyncio
async def test_export_open(exception, log_message, tmpdir):
    f = str(tmpdir.join("path"))
    e = export.Export()
    with taddons.context() as tctx:
        with mock.patch("mitmproxy.addons.export.open") as m:
            m.side_effect = exception(log_message)
            e.file("raw", tflow.tflow(resp=True), f)
            assert await tctx.master.await_log(log_message, level="error")


@pytest.mark.asyncio
async def test_clip(tmpdir):
    e = export.Export()
    with taddons.context() as tctx:
        with pytest.raises(exceptions.CommandError):
            e.clip("nonexistent", tflow.tflow(resp=True))

        with mock.patch('pyperclip.copy') as pc:
            e.clip("raw", tflow.tflow(resp=True))
            assert pc.called

        with mock.patch('pyperclip.copy') as pc:
            e.clip("curl", tflow.tflow(resp=True))
            assert pc.called

        with mock.patch('pyperclip.copy') as pc:
            e.clip("httpie", tflow.tflow(resp=True))
            assert pc.called

        with mock.patch('pyperclip.copy') as pc:
            log_message = "Pyperclip could not find a " \
                          "copy/paste mechanism for your system."
            pc.side_effect = pyperclip.PyperclipException(log_message)
            e.clip("raw", tflow.tflow(resp=True))
            assert await tctx.master.await_log(log_message, level="error")
