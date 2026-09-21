"""Instrument a target application so its SQL joins the recording.

    agent = instrument(app, engine, api_url="http://127.0.0.1:8000")
    ...
    await agent.start()      # from the app's lifespan

Flow of one captured request:

    proxy ──(x-infrareplay-recording-id, -correlation-id)──> app
                                                              │ SQL
                                                    capture plugin queue
    ingest API  <──── flush(), before the response is sent ────┘

Flushing *before* the response leaves the app is deliberate: the proxy then
never records a response whose database events have not landed yet, so no
polling or sleeping is needed anywhere.
"""

from infrareplay.agent.agent import CaptureAgent, CaptureMiddleware, instrument

__all__ = ["CaptureAgent", "CaptureMiddleware", "instrument"]
