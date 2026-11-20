"""LOCAL demo data for the UI scenarios and screenshots (SQLite only; refuses any other database).

Never run against production. Usage (from the repo root, with the API env pointing at a throwaway SQLite file):
    python dashboard/scripts/ux/seed_demo.py .
Videos are cut from tests/fixtures; names are fictional demo channels.
"""
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

assert os.environ["ASC_DATABASE_URL"].startswith("sqlite:///"), "demo seed is local-only"
ROOT = Path(sys.argv[1])
STORAGE = Path(os.environ["ASC_STORAGE_DIR"])

from clipper.db import init_engine, session_scope  # noqa: E402
init_engine(os.environ["ASC_DATABASE_URL"])
from clipper.models import (Candidate, Clip, PublishAccount, Publication, PublishRule, SituationRow,  # noqa: E402
                            Streamer, StreamSession, Worker)

now = datetime.utcnow()
FIX = ROOT / "tests" / "fixtures"
SRC = [FIX / "chase_crash.mp4", FIX / "gameplay_event.mp4", FIX / "loud_game_noise.mp4"]


def render(cid: int, i: int) -> tuple[str, str, int]:
    d = STORAGE / "clips" / str(cid)
    d.mkdir(parents=True, exist_ok=True)
    v, t = d / "clip_v1_demo.mp4", d / "thumb_v1_demo.jpg"
    src = SRC[i % 3]
    vf = f"scale=540:960:force_original_aspect_ratio=increase,crop=540:960,hue=h={(i * 37) % 360}"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(2 + i), "-t", "14", "-i", str(src), "-vf", vf,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-c:a", "aac", "-b:a", "64k",
                    "-movflags", "+faststart", str(v)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "4", "-i", str(v), "-frames:v", "1", "-q:v", "5", str(t)],
                   check=True)
    return f"clips/{cid}/{v.name}", f"clips/{cid}/{t.name}", v.stat().st_size


CLIPS = [
    # title, status, score, gate, situation idx, reasons
    ("Fuga pela orla termina no píer", "ready", 91, "", 0, ["Perseguição com sirene e narração acelerada",
                                                             "Reação forte do streamer no clímax"]),
    ("Policial cai na conversa do mecânico", "ready", 86, "", 2, ["Risada longa no chat de voz",
                                                                  "Diálogo com virada clara"]),
    ("Tiroteio na garagem do condomínio", "ready", 82, "HUMAN_REVIEW", 1, ["Sequência de tiros e gritos",
                                                                         "A IA não confirmou o desfecho"]),
    ("Assalto à joalheria dá errado", "ready", 78, "", None, ["Alarme e correria", "Fala curta e direta"]),
    ("Susto com o caminhão na contramão", "ready", 74, "", None, ["Grito súbito", "Pico de áudio"]),
    ("Negociação no telhado", "approved", 88, "", 0, ["Tensão crescente", "Desfecho claro"]),
    ("Corrida de lancha até a ilha", "approved", 80, "", None, ["Velocidade e narração"]),
    ("Encontro na lanchonete", "rendering", 70, "", None, ["Conversa engraçada"]),
    ("Briga no posto de gasolina", "rejected", 61, "", None, ["Áudio confuso"]),
    ("Perseguição no túnel", "failed", 72, "", None, ["Render falhou: arquivo de origem indisponível"]),
]


def main() -> None:
    with session_scope() as s:
        a = Streamer(platform="twitch", channel="lua_vilela_demo", url="https://www.twitch.tv/lua_vilela_demo",
                     display_name="Lua Vilela", status="live", stream_title="Cidade Alta RP: dia de patrulha",
                     last_live_at=now)
        b = Streamer(platform="youtube", channel="theobrasa_demo", url="https://www.youtube.com/@theobrasa_demo",
                     display_name="Theo Brasa", status="offline")
        s.add_all([a, b])
        s.flush()
        live = StreamSession(streamer_id=a.id, status="live", title="Cidade Alta RP: dia de patrulha",
                             started_at=now - timedelta(hours=1, minutes=32), covered_until=5520, analyzed_until=5490,
                             scored_until=5480, segment_count=920,
                             stats={"story_until": 5460, "situations_open": 1})
        old = StreamSession(streamer_id=b.id, status="ended", title="Noite de corridas",
                            started_at=now - timedelta(days=1, hours=4), ended_at=now - timedelta(days=1),
                            covered_until=10800, analyzed_until=10800)
        s.add_all([live, old])
        s.flush()
        sits = [
            SituationRow(session_id=live.id, kind="police_chase", start=3010, last=3195, status="closed",
                         confidence=0.84,
                         summary="Perseguição policial (50:10–53:15): sirene → fuga pela orla → batida no píer → abordagem",
                         data={"complete": True, "outcome": "arrested", "climax_t": 3150,
                               "participants": ["Lua Vilela", "Policial Ramos"], "goals": ["fugir da abordagem"],
                               "conflicts": ["polícia contra fugitiva"], "changes": ["carro destruído no píer"],
                               "uncertain": [],
                               "events": [{"t": 3010, "phase": "setup", "label": "Sirene ligada atrás do carro", "source": "áudio"},
                                          {"t": 3060, "phase": "development", "label": "Fuga pela avenida da orla", "source": "visão"},
                                          {"t": 3120, "phase": "development", "label": "Narração acelerada", "source": "fala"},
                                          {"t": 3150, "phase": "climax", "label": "Batida no píer", "source": "áudio"},
                                          {"t": 3190, "phase": "resolution", "label": "Abordagem policial", "source": "fala"}]}),
            SituationRow(session_id=live.id, kind="shootout", start=4200, last=4290, status="closed", confidence=0.58,
                         summary="Tiroteio (70:00–71:30): tiros na garagem → gritos → corte da câmera",
                         data={"complete": False, "outcome": "none", "climax_t": 4250, "participants": ["Lua Vilela"],
                               "uncertain": ["quem iniciou os tiros", "desfecho"], "goals": [], "conflicts": [],
                               "changes": [],
                               "events": [{"t": 4200, "phase": "setup", "label": "Entrada na garagem", "source": "visão"},
                                          {"t": 4250, "phase": "climax", "label": "Sequência de tiros", "source": "áudio"}]}),
            SituationRow(session_id=live.id, kind="funny_conversation", start=5100, last=5440, status="ongoing",
                         confidence=0.71, summary="Conversa engraçada (85:00–90:40): mecânico engana o policial",
                         data={"complete": None, "outcome": "laughter", "climax_t": 5380,
                               "participants": ["Lua Vilela", "Mecânico Juca", "Policial Ramos"],
                               "uncertain": [], "goals": ["evitar a multa"], "conflicts": [], "changes": [],
                               "events": [{"t": 5100, "phase": "setup", "label": "Policial para o guincho", "source": "fala"},
                                          {"t": 5300, "phase": "development", "label": "Mecânico inventa desculpa", "source": "fala"},
                                          {"t": 5380, "phase": "climax", "label": "Risada geral", "source": "áudio"}]}),
        ]
        s.add_all(sits)
        s.flush()
        for d, n in {"accepted": 10, "below_min_score": 23, "text_only": 9, "duplicate": 4, "story_merged": 3}.items():
            for k in range(n):
                s.add(Candidate(session_id=live.id, streamer_id=a.id, peak=100.0 * k, score=40.0, decision=d))
        ids = []
        for i, (title, status, score, gate, si, reasons) in enumerate(CLIPS):
            st = 2990 + i * 260
            c = Clip(title=title, session_id=live.id if i < 8 else old.id, streamer_id=a.id if i < 8 else b.id, status=status,
                     start=st, end=st + 32 + i, peak=st + 18, score=score, gate=gate, reasons=reasons,
                     score_breakdown={"intensity": 8.2 - i * 0.3, "surprise": 6.5, "humor": 3.0 + (i % 3) * 2,
                                      "reaction": 7.4, "narrative": 6.0 + (i % 2), "visual": None},
                     transcript="— Para o carro! — Nem pensar, vou pela orla… segura!",
                     title_suggestions=[title, title.split(" ")[0] + " inesperada na cidade"],
                     description="Corte da live de hoje.", hashtags=["gtarp", "cidadealta"],
                     prelim_score=score - 4, editorial_score=score if status in ("approved", "ready") else None,
                     confidence=0.8, situation_id=sits[si].id if si is not None else None, version=1,
                     provenance={"story": {"action": "story" if si is not None else "isolated"}},
                     error="Arquivo de origem indisponível" if status == "failed" else "",
                     created_at=now - timedelta(minutes=12 * (10 - i)))
            s.add(c)
            s.flush()
            if status not in ("rendering", "failed"):
                c.video_path, c.thumb_path, c.file_size = render(c.id, i)
            ids.append(c.id)
        for label, host, roles in (("201", "vision-201", ["vision"]), ("206", "capture-206", ["capture", "audio", "render"]),
                                   ("210", "core-210", ["scoring", "vision", "publisher"])):
            s.add(Worker(id=f"demo-{label}", host=host, host_label=label, roles=roles, version="0.6.3",
                         status="online", last_heartbeat_at=now + timedelta(days=2)))  # demo: no real worker sends heartbeats
        yt = PublishAccount(platform="youtube", external_id="demo-yt", name="Lua Vilela Cortes", handle="@luavilelacortes",
                            status="connected", meta={"privacy_options": ["private", "unlisted", "public"]})
        ig = PublishAccount(platform="instagram", external_id="demo-ig", name="Lua Vilela", handle="@luavilela.rp",
                            status="connected")
        s.add_all([yt, ig])
        s.flush()
        rule = PublishRule(account_id=yt.id, mode="scheduled", require_approval=True, min_score=75, privacy="private",
                           max_per_day=3, slots=["12:00", "18:30", "21:00"])
        s.add(rule)
        s.flush()
        day = now.replace(hour=15, minute=0, second=0, microsecond=0)
        for k, (ci, acc, status, origin, dt) in enumerate([
            (5, yt, "scheduled", "manual", day + timedelta(days=1)),
            (6, yt, "scheduled", "auto", day + timedelta(days=1, hours=6, minutes=30)),
            (5, ig, "scheduled", "manual", day + timedelta(days=2, hours=6)),
            (0, yt, "proposed", "auto", day + timedelta(days=3)),
            (1, ig, "proposed", "auto", day + timedelta(days=3, hours=6)),
            (6, ig, "published", "manual", day - timedelta(days=1)),
        ]):
            s.add(Publication(clip_id=ids[ci], account_id=acc.id, rule_id=rule.id if origin == "auto" else None,
                              platform=acc.platform, status=status, origin=origin, scheduled_at=dt,
                              title=CLIPS[ci][0], privacy="private", remote_url="" if status != "published" else
                              "https://www.instagram.com/", metrics={"views": 1840, "likes": 212} if status == "published" else {},
                              published_at=dt if status == "published" else None))
    print("seeded", ids)


main()
