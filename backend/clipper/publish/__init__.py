"""Phase 4: auto publisher (YouTube Shorts, TikTok, Instagram Reels) through the official APIs.

Safety model:
* nothing is published without a connected, authorised account and an eligible clip
  (never manual test clips, never rejected or unanalysed clips, streamer republish rights);
* human approval is required by default; automatic PUBLIC posting needs an explicit admin opt-in;
* a clip is uploaded at most once per account (DB unique constraint + resumable sessions);
* OAuth tokens and client secrets are encrypted at rest and never returned by the API.
"""
