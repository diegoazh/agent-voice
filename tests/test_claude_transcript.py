import json
import os

from agent_voice.adapters import claude


def entry(role, *blocks, sidechain=False, kind=None):
    return {
        "type": kind or role,
        "isSidechain": sidechain,
        "message": {"role": role, "content": list(blocks)},
    }


def text(t):
    return {"type": "text", "text": t}


def thinking(t):
    return {"type": "thinking", "thinking": t}


def tool_use():
    return {"type": "tool_use", "id": "x", "name": "Bash", "input": {}}


def tool_result():
    return {"type": "tool_result", "tool_use_id": "x", "content": "out"}


def write_session(config_dir, project, name, entries, mtime=None, raw_lines=()):
    folder = config_dir / "projects" / project
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.jsonl"
    lines = [json.dumps(e) for e in entries] + list(raw_lines)
    path.write_text("\n".join(lines) + "\n")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_returns_the_final_assistant_text_entry_only(tmp_path):
    write_session(
        tmp_path,
        "-proj",
        "s1",
        [
            entry("user", text("question")),
            entry("assistant", text("early note")),
            entry("assistant", tool_use()),
            entry("user", tool_result()),
            entry("assistant", thinking("hidden")),
            entry("assistant", text("final A"), text("final B")),
        ],
    )
    assert claude.last_reply([tmp_path]) == "final A\nfinal B"


def test_skips_trailing_entries_without_text(tmp_path):
    write_session(
        tmp_path,
        "-proj",
        "s1",
        [
            entry("assistant", text("the reply")),
            entry("assistant", thinking("later thought")),
            entry("assistant", tool_use()),
            {"type": "system", "subtype": "x"},
        ],
    )
    assert claude.last_reply([tmp_path]) == "the reply"


def reply(t):
    return [entry("assistant", text(t))]


def test_picks_the_most_recently_modified_session(tmp_path):
    write_session(tmp_path, "-a", "old", reply("old"), mtime=1000)
    write_session(tmp_path, "-b", "new", reply("new"), mtime=2000)
    write_session(tmp_path, "-a", "mid", reply("mid"), mtime=1500)
    assert claude.last_reply([tmp_path]) == "new"


def test_searches_all_config_dirs(tmp_path):
    personal, work = tmp_path / "p", tmp_path / "w"
    write_session(personal, "-a", "s", reply("personal"), mtime=1000)
    write_session(work, "-a", "s", reply("work"), mtime=2000)
    assert claude.last_reply([personal, work]) == "work"
    assert claude.last_reply([work, personal]) == "work"


def test_missing_or_empty_config_dirs_give_none(tmp_path):
    (tmp_path / "empty").mkdir()
    assert claude.last_reply([tmp_path / "nope", tmp_path / "empty"]) is None
    assert claude.last_reply([]) is None


def test_session_without_any_reply_text_gives_none(tmp_path):
    write_session(tmp_path, "-a", "s", [entry("assistant", tool_use())])
    assert claude.last_reply([tmp_path]) is None


def test_cwd_prefers_its_project_even_if_older(tmp_path):
    write_session(tmp_path, "-Users-me-my-proj", "s", reply("mine"), mtime=1000)
    write_session(tmp_path, "-other", "s", reply("other"), mtime=2000)
    assert claude.last_reply([tmp_path], cwd="/Users/me/my_proj") == "mine"


def test_cwd_without_project_folder_falls_back_to_newest(tmp_path):
    write_session(tmp_path, "-other", "s", reply("other"), mtime=2000)
    assert claude.last_reply([tmp_path], cwd="/Users/me/unknown") == "other"


def test_cwd_project_in_older_config_dir_still_preferred(tmp_path):
    personal, work = tmp_path / "p", tmp_path / "w"
    write_session(personal, "-Users-me-proj", "s", reply("mine"), mtime=1000)
    write_session(work, "-elsewhere", "s", reply("other"), mtime=2000)
    assert claude.last_reply([personal, work], cwd="/Users/me/proj") == "mine"


def test_sidechain_entries_are_ignored(tmp_path):
    write_session(
        tmp_path,
        "-a",
        "s",
        [
            entry("assistant", text("main reply")),
            entry("assistant", text("subagent reply"), sidechain=True),
        ],
    )
    assert claude.last_reply([tmp_path]) == "main reply"


def test_subagent_transcript_files_are_excluded(tmp_path):
    write_session(tmp_path, "-a", "s", reply("main"), mtime=1000)
    nested = tmp_path / "projects" / "-a" / "s" / "subagents"
    nested.mkdir(parents=True)
    sub = nested / "agent-1.jsonl"
    sub.write_text(json.dumps(entry("assistant", text("sub"), sidechain=True)) + "\n")
    os.utime(sub, (3000, 3000))
    assert claude.last_reply([tmp_path]) == "main"


def test_session_made_only_of_sidechain_entries_gives_none(tmp_path):
    write_session(tmp_path, "-a", "s", [entry("assistant", text("x"), sidechain=True)])
    assert claude.last_reply([tmp_path]) is None


def test_malformed_and_odd_lines_are_skipped(tmp_path):
    write_session(
        tmp_path,
        "-a",
        "s",
        reply("good"),
        raw_lines=[
            "{not json",
            "",
            "[1, 2]",
            '"str"',
            json.dumps({"type": "assistant", "message": "oops"}),
            json.dumps({"type": "assistant", "message": {"content": "plain"}}),
            json.dumps({"type": "assistant", "message": {"content": [3, {"type": "text"}]}}),
            json.dumps({"type": "user", "message": {"content": [text("not assistant")]}}),
        ],
    )
    assert claude.last_reply([tmp_path]) == "good"


def test_reading_never_writes(tmp_path):
    session = write_session(tmp_path, "-a", "s", reply("hi"), mtime=1000)

    def snapshot():
        return sorted(
            (str(p.relative_to(tmp_path)), p.stat().st_mtime_ns, p.stat().st_size)
            for p in [tmp_path, *tmp_path.rglob("*")]
        )

    before = snapshot()
    assert claude.last_reply([tmp_path], cwd="/a") == "hi"
    assert snapshot() == before
    assert session.read_text().count("\n") == 1


def test_reads_only_the_tail_of_a_large_file(tmp_path, monkeypatch):
    filler = [entry("user", text("x" * 200)) for _ in range(3000)]
    session = write_session(tmp_path, "-a", "s", filler + reply("tail reply"))
    size = session.stat().st_size
    read = []
    real_open = open

    class Counting:
        def __init__(self, fh):
            self.fh = fh

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.fh.close()

        def __getattr__(self, name):
            attr = getattr(self.fh, name)
            if name != "read":
                return attr

            def counted(n=-1):
                data = attr(n)
                read.append(len(data))
                return data

            return counted

    monkeypatch.setattr(claude, "open", lambda *a, **k: Counting(real_open(*a, **k)), raising=False)
    assert claude.last_reply([tmp_path]) == "tail reply"
    assert sum(read) < size // 4


def test_lines_and_multibyte_text_spanning_block_boundaries(tmp_path, monkeypatch):
    monkeypatch.setattr(claude, "_BLOCK", 7)
    long = "canción ñandú " * 20
    write_session(
        tmp_path,
        "-a",
        "s",
        [entry("assistant", text("older")), entry("assistant", text(long))],
    )
    assert claude.last_reply([tmp_path]) == long


def test_file_without_trailing_newline_and_single_line(tmp_path, monkeypatch):
    monkeypatch.setattr(claude, "_BLOCK", 5)
    folder = tmp_path / "projects" / "-a"
    folder.mkdir(parents=True)
    (folder / "s.jsonl").write_text(json.dumps(entry("assistant", text("solo"))))
    assert claude.last_reply([tmp_path]) == "solo"


def test_project_folder_encoding_replaces_every_non_alphanumeric(tmp_path):
    assert claude.encode_project_dir("/Users/me/v1.2_x y") == "-Users-me-v1-2-x-y"
    write_session(tmp_path, "-Users-me-v1-2-x-y", "s", reply("mine"), mtime=1000)
    write_session(tmp_path, "-other", "s", reply("other"), mtime=2000)
    assert claude.last_reply([tmp_path], cwd="/Users/me/v1.2_x y") == "mine"


def test_only_text_typed_blocks_are_spoken(tmp_path):
    write_session(
        tmp_path,
        "-a",
        "s",
        [
            entry("assistant", text("spoken")),
            entry("assistant", {"type": "thinking", "text": "hidden"}),
        ],
    )
    assert claude.last_reply([tmp_path]) == "spoken"
