# Site-skills library version record

Vendored (not referenced live) so experiment runs depend on a static,
known skill version. Update by re-running the sync command below and
refreshing this record in the same commit.

- Source: browser-harness repo, `agent-workspace/domain-skills/`
  (local checkout `~/lexmount/browser-harness`,
  upstream https://github.com/browser-use/browser-harness)
- Source commit: `8d0684a926650905c45bb2b0da0b48f7e86bd71c`
  (branch tip containing the 2026-07-07 lexbench report; skill content last
  touched by `c9ea510` "add image.baidu.com and fanyi.baidu.com skills")
- Synced: 2026-07-07
- Contents: 182 site directories, 203 files (191 skill .md, 8 `hosts` alias
  files, 2 .py helper scripts referenced by their skill docs, 2 .gitkeep).
  Only `*.md` files are ever matched/injected; the .py helpers are kept so
  the snapshot stays byte-identical to the source for future syncs.
- Verified: `diff -rq` clean against the source at sync time

Sync command:

```bash
rsync -a --delete ~/lexmount/browser-harness/agent-workspace/domain-skills/ \
    browseruse_bench/agents/site_skills/domain-skills/
```
