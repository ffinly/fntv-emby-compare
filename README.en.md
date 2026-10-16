# FNTV Emby Compare

[简体中文](README.md) | [繁體中文](README.zh-TW.md) | **English**

---

🔍 **Compare media identification between FNTV and Emby.**

When the same media is indexed by both FNTV and Emby, the services may identify different titles or leave TMDb IDs missing. This tool matches movies and entire TV series by library name and paths relative to each library, then compares their TMDb IDs to highlight entries for manual review and correction.

Media may reside on the same or different cloud drives or mount points. Directory names do not need to contain `{tmdb-xxx}`. No TMDb API access or TMDb API Key is required.

**Read-only checks: no changes to the FNTV database, no writes to Emby, and no automatic repairs.** The database is read through a local temporary snapshot, which is cleaned up after the task. Results are printed only to the runtime log.

## ✨ Features

- 📚 Pair libraries with matching names. Libraries present on only one side are listed and skipped; checks stop if duplicate library names prevent unique pairing.
- 🗂️ Compare movies and entire TV series using actual paths or library-relative paths, supporting different mount prefixes and some STRM media-source scenarios.
- ⚠️ Report mismatched or missing TMDb IDs at corresponding paths, retaining both services' item IDs and paths for manual investigation.
- 📋 Count shared and one-sided TMDb IDs separately for movies and entire TV series. Distinguish raw ID set differences from IDs remaining after excluding same-path mismatches.
- 🔎 Flag ambiguous paths and directory-only candidates whose files have not been verified, without forcing them to count as confirmed matches.
- 🛡️ Access the FNTV database read-only, using temporary snapshots and integrity checks; compatible with read-only directory mounts in Qinglong containers.
- 🌐 Support Emby over HTTP/HTTPS with HTTPS certificate verification enabled. Send the API Key in a request header, not in the URL.
- 📝 Show a single line for libraries without differences, followed by a summary and details for libraries with issues. Omit zero-valued anomaly counts from the final summary.

## 📁 Files

```text
fntv-emby-compare/
├── fntv_emby_compare.py  # Main script
├── requirements.txt     # Python dependencies
├── LICENSE              # GNU GPL v3.0 license
├── README.md               # Simplified Chinese (default)
├── README.zh-TW.md         # Traditional Chinese
└── README.en.md            # English
```

## 🚀 Usage

### 1. Install dependencies

Python 3.10 or later is recommended to install the current version of `requests`:

```bash
python3 -m pip install -r requirements.txt
```

In Qinglong, install `requests` under dependency management → Python3. All other dependencies are part of the Python standard library.

### 2. Mount the FNTV database directory read-only in Qinglong

Add this mount under `volumes` in your existing Qinglong service, keeping its other settings:

```yaml
services:
  qinglong:
    volumes:
      - /usr/local/apps/@appdata/trim.media/database:/ql/trim.media.database:ro
```

Replace `qinglong` with your actual service name, then recreate the container using your existing deployment method. Adjust the host path to match your FNTV installation.

- Host directory: `/usr/local/apps/@appdata/trim.media/database`
- Database inside the container: `/ql/trim.media.database/trimmedia.db`
- `:ro`: prevent the container from modifying the source database directory.

Mount the entire database directory rather than just `trimmedia.db`, so accompanying files such as `-wal` and `-shm` can be read. The container user needs read permission, and its temporary directory must be writable with enough free space.

The script first attempts to obtain a snapshot using SQLite backup. If the read-only mount cannot be opened directly, it tries copying the database and any existing WAL/journal files into a temporary directory, checks file states before and after copying, and runs `quick_check`. Checks stop if a stable snapshot cannot be obtained or validation fails; the source database is not modified.

### 3. Configure environment variables

Add these variables in Qinglong's environment variable settings:

| Variable | Example | Description |
| --- | --- | --- |
| `EMBY_URL` | `https://emby.example.com:8443` | Required. Emby server URL, supporting HTTP/HTTPS |
| `EMBY_API_KEY` | `your Emby API Key` | Required. API Key created in the Emby administration dashboard |
| `FNTV_DB` | `/ql/trim.media.database/trimmedia.db` | Optional. Database path inside the container; defaults to this path |

For a local terminal, you can use:

```bash
export EMBY_URL='https://emby.example.com:8443'
export EMBY_API_KEY='your Emby API Key'
export FNTV_DB='/ql/trim.media.database/trimmedia.db'
```

Use HTTPS when connecting over the public internet; HTTP sends the Key in the request header without encryption. The script does not automatically use system proxies or follow redirects. Enter the final, directly accessible Emby URL.

### 4. Run

Upload `fntv_emby_compare.py` and use this Qinglong task command:

```bash
task fntv_emby_compare.py
```

To run locally:

```bash
python3 -u fntv_emby_compare.py
```

If possible, avoid running while either service is performing large scans or metadata scraping, to reduce changes to the data during reads.

## 📊 Example output

All data below is fictional: titles, TMDb IDs, item IDs, paths, and counts do not represent an actual media library. This example is translated for readability; the script's actual logs remain in Simplified Chinese.

```text
Libraries with matching names (2):
TV Series, Movies
Skipped FNTV-only libraries: None
Skipped Emby-only libraries: None

========== Start comparison ==========
✅ TV Series: 5 shared TMDB IDs (entire TV series), no ID set differences
⚠️ Movies: 1 same-path ID mismatch; 1 Emby-only ID after excluding same-path mismatches
Movies (deduplicated by TMDB ID): FNTV 10, Emby 11; shared TMDB IDs 9
  Raw ID set differences (including separately reported path mismatches and directory candidates): FNTV 1; Emby 2
Emby-only movie TMDB IDs (path mismatches and directory candidates listed separately):
  Example Movie C | TMDB: 900000003 | Item: emby-example-02
    Path: Demo Category/Example Movie C/Example Movie C.mkv
File/series directory paths match, but TMDB IDs differ or are missing: 1
Match method: identical library-relative paths
Emby:
  Example Movie A | TMDB: 900000001 | Item: emby-example-01
FNTV:
  Example Movie B | TMDB: 900000002 | Item: fntv-example-01
  Shared path: Demo Category/Example Movie A/Example Movie A.mkv

========== Final summary ==========
Libraries compared: 2 (counts below are summed across libraries)
Movies (deduplicated by TMDB ID): FNTV 10; Emby 11; shared TMDB IDs 9
  Raw ID set differences (including path mismatches and directory candidates, FNTV/Emby): 1/2
Entire TV series (deduplicated by TMDB ID): FNTV 5; Emby 5; shared TMDB IDs 5
⚠️ File/series directory paths match, but TMDB IDs differ or are missing: 1
📋 One-sided IDs after excluding same-path mismatches (FNTV/Emby): 0/1
ID differences do not confirm identification errors; please review manually.
One-sided IDs may result from media not yet indexed or different scan scopes; unmatched entries remain unverified.
```

## 🔎 Understanding the results

- **Same-path ID mismatch**: IDs at corresponding paths differ, or at least one side has no ID. Manually check which service, if either, identified the title incorrectly.
- **Raw ID set differences**: an ID appears in only one of the paired libraries. This may include path mismatches already reported separately, so it does not necessarily equal the number of individually listed one-sided entries.
- **One-sided IDs after excluding same-path mismatches**: IDs remaining after excluding mismatches with corresponding paths. Possible causes include media not yet indexed, different scan scopes, or identification issues. Unverified directory candidates are not excluded as confirmed matches.
- **Directory candidates**: directories match, but the actual files have not been verified. These are not confirmed matches for the same media.
- **Entries without a library association**: FNTV movie/series records not associated with a library are excluded from comparison. A warning appears in the summary when such entries exist.

Counts are deduplicated by TMDb ID within each library, then summed across libraries in the final summary—not globally deduplicated across all libraries. Multiple versions of a title may share one TMDb ID.

**Identical ID sets do not mean every file was identified correctly. Both services can assign the same incorrect ID without producing a difference.** Directory tags such as `{tmdb-xxx}` are not treated as ground truth; the final judgment is yours.

## ⚠️ Scope and limitations

- Compare only movies and entire TV series, not season or episode IDs. Video content, file sizes, and series completeness are not verified.
- Intended primarily for libraries with matching names and corresponding directory structures. Different cloud drives are supported, but different category hierarchies or renamed paths may prevent matches.
- STRM support depends on the paths and media-source information returned by Emby. If actual paths cannot be recovered, entries are reported as unmatched or directory candidates.
- Directory names do not need TMDb IDs. This version does not additionally compare directory ID tags against either service's identification.
- Changes to the FNTV database schema or Emby API may require adaptation. Checks stop if required fields are missing or pagination is inconsistent, to avoid reporting incomplete results.
- Execution failures return exit code `1`. A completed comparison returns exit code `0` even when identification differences are found; differences are reported in the log.

## 🛠️ FAQ

**Database not found**: check the host directory, container mount, and `FNTV_DB`. The script reads the path inside the container.

**The read-only mount cannot be opened directly**: the script is attempting a temporary snapshot; this message does not necessarily mean failure. If errors follow, check read permissions, temporary space, and continuous database writes. Do not change the mount to writable to work around this message.

**HTTPS certificate error**: check the domain, certificate chain, certificate expiration, and container clock, or configure a trusted CA for a trusted self-signed certificate. Disabling verification is not recommended.

**One-sided ID counts do not match the number of individually listed entries**: check the same-path mismatch and directory candidate sections. Those entries are listed separately to avoid duplicate output. Unique ID counts may also differ from the number of entries when multiple versions exist.

**No libraries with matching names**: check that library names match on both services. Libraries present only on one side, such as courses or collections, are skipped; none are automatically created or modified.

## 📈 Star History

<a href="https://www.star-history.com/?repos=ffinly%2Ffntv-emby-compare&amp;type=date&amp;legend=top-left">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=ffinly/fntv-emby-compare&amp;type=date&amp;theme=dark&amp;legend=top-left" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=ffinly/fntv-emby-compare&amp;type=date&amp;legend=top-left" />
    <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=ffinly/fntv-emby-compare&amp;type=date&amp;legend=top-left" />
  </picture>
</a>

## 📄 License

Original code in this project is licensed under the [GNU General Public License v3.0](LICENSE) (GPL-3.0-only).
