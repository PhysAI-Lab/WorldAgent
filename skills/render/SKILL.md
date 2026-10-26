---
name: render
description: Render the final Cycles sequence (1920x1080 unless the plugin's resolution setting changes it) of a WorldAgent candidate with the plugin's render tool and verify the render and media rows. Use /worldagent:promote next.
argument-hint: <case_dir> [candidate.blend] [--preview --frames <list>]
disable-model-invocation: true
---

**Host compatibility (Codex fallback).** Keep Claude Code's native token substitution. If `${CLAUDE_PLUGIN_ROOT}` is still visible literally, replace it with the absolute path from `WorldAgent plugin root:` in the session-start report before reading files or running commands, including paths in referenced documents; quote the resolved paths. If there is no session-start report (for example, the plugin hooks are not trusted yet), the plugin root is the folder two levels above the directory that contains this SKILL.md. If `$ARGUMENTS` is still visible literally, it means the user text supplied with this skill, preserving its quoted arguments. Do not rely on those tokens being shell environment variables.

Codex only: shell exports from SessionStart are not assumed. Set `WORLDAGENT_ENV_FILE` to the `WorldAgent session environment:` path from that report for shell commands. Where the body refers to an unset `WORLDAGENT_*` variable (including assets and memory/time budgets), use its resolved value from the report. Use `WORLDAGENT_*` or the WorldAgent config file instead of Claude's `/plugin configure`; invoke `/worldagent:<name>` commands as `$worldagent:<name>`.

# Render a WorldAgent candidate

Arguments: `$ARGUMENTS` — the case directory, optionally the candidate `.blend`, and optionally `--preview --frames <list>` for preview frames instead of the final sequence. Without a candidate, use the one every passing row's `candidate` field names when they agree; otherwise ask.

1. Read `<case_dir>/manifest.json`. For a final render, every row other than the render and media rows should pass on this candidate (run `python3 ${CLAUDE_PLUGIN_ROOT}/bin/gate.py <case_dir> --candidate <candidate>`). If some do not, say which and ask whether to render anyway; a final render of a failing candidate cannot be promoted.
2. Render with `python3 ${CLAUDE_PLUGIN_ROOT}/bin/render_candidate.py <case_dir> --candidate <candidate>`. Add `--encode` for a final render (it encodes the frames into `media/final_<stamp>.mp4` after rendering), `--flip` when the scene has a FLIP domain, `--samples N` within any time budget the user gave, and the preview arguments when asked. If a final render already exists without a movie, encode it with `render_candidate.py <case_dir> --encode-report reports/render_<stamp>.json` instead of rendering again. Never render or encode with a one-off script. A `blocked` render (render device not available) stops here; report it. If only the movie is `blocked` (ffmpeg or ffprobe not found), the frames are kept: report it, and once `WORLDAGENT_FFMPEG` / `WORLDAGENT_FFPROBE` are set or the tools are on PATH, encode with `--encode-report` instead of rendering again.
3. For a final render, verify the render and media rows through the runner, as in `/worldagent:verify` step 2, with the new `reports/render_<stamp>.json`, its `media/final_<stamp>/` folder and `media/final_<stamp>.mp4` as `--input`. Their verifiers use `worldagent_measure.render.check_render_report` for the frames and `worldagent_measure.render.check_movie` for the movie (`references/writing-verifiers.md`).
4. Report and stop: mode, device, samples, frame count, render time, the output folder, the movie and its `movie` status in the report, and the verdicts of the render and media rows. Do not promote. Suggest `/worldagent:promote <case_dir>` when every row passes.
