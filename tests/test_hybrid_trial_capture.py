import json

import httpx
import pytest

from trial_anthropic_proxy import MetadataStream


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [
    {'choices':[{'message':{'content':'test'}}], 'usage':{'prompt_tokens':2,'completion_tokens':1}},
    {'type':'message','content':[{'type':'text','text':'test'}],'usage':{'input_tokens':2,'output_tokens':1}},
])
async def test_optional_capture_sink_cannot_break_complete_provider_body(payload):
    raw = json.dumps(payload).encode()
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield raw[:7]
            yield raw[7:]
    row = {}
    response = httpx.Response(200, stream=MetadataStream(Stream(), row, None))
    try:
        assert await response.aread() == raw
        assert response.json() == payload
        assert row['body_complete'] is True
    finally:
        await response.aclose()
