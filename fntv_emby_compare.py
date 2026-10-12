# -*- coding: utf-8 -*-
"""FNTV Emby Compare：飞牛影视与 Emby 媒体识别对比工具。

用于同一份媒体在飞牛影视与 Emby 建库后的识别核对；媒体可以位于相同
或不同网盘、挂载点。按同名媒体库及库内相对路径匹配电影和整部剧集，
列出 TMDb ID 不一致、ID 缺失、单边独有及尚未核实的目录候选，辅助人工修正。

仅从飞牛数据库和 Emby API 读取数据，不修改双方媒体库，不自动修复。
数据库读取会使用本地临时快照，任务结束后清理；检查结果只输出到日志。
不比较季、单集 ID，也不要求目录包含 {tmdb-xxx}，不需要 TMDb API Key。
ID 一致并不保证识别正确，目录候选及单边 ID 差异需要人工确认。

青龙环境变量：
    EMBY_URL      Emby HTTP/HTTPS 地址，例如 https://emby.example.com:8443
    EMBY_API_KEY  Emby API Key，通过 X-Emby-Token 请求头发送
    FNTV_DB       飞牛数据库在容器内的路径
                  默认 /ql/trim.media.database/trimmedia.db
建议将飞牛数据库整个目录以 :ro 挂载到青龙容器，保留 WAL 等配套文件。

运行：
    task fntv_emby_compare.py
    python3 -u fntv_emby_compare.py

依赖与配置步骤见 README.md；本项目采用 GNU GPL v3.0，详见 LICENSE。
"""

import collections
import contextlib
import json
import os
import posixpath
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

import requests

EMBY_BASE_URL = os.getenv('EMBY_URL')
EMBY_API_KEY = os.getenv('EMBY_API_KEY')
FNTV_DB = os.getenv('FNTV_DB', '/ql/trim.media.database/trimmedia.db')
PAGE_SIZE = 500


def tmdb_id(value):
    text = str(value or '').strip()
    return str(int(text)) if text.isdigit() and int(text) > 0 else ''


def normalize_path(value):
    value = unicodedata.normalize(
        'NFC', str(value or '').strip()
    ).replace(chr(92), '/')
    return posixpath.normpath(value) if value else ''


def series_root(directory):
    """提取整部剧集目录，不比较季或单集的 TMDB ID。"""
    path = normalize_path(directory)
    parts = path.split('/')
    for index, part in enumerate(parts):
        if re.fullmatch(
            r'(?:Season\s*\d+|S\d+|Specials|第\s*\d+\s*季)',
            part, re.I
        ):
            return '/'.join(parts[:index]) or '/'
    tagged = [
        i for i, part in enumerate(parts)
        if re.search(r'\{tmdb-\d+\}', part, re.I)
    ]
    if tagged:
        return '/'.join(parts[:tagged[-1] + 1])
    return path


def fingerprint(path):
    result = []
    for suffix in ('', '-wal', '-journal'):
        file = Path(str(path) + suffix)
        try:
            st = file.stat()
            result.append((
                suffix, st.st_ino, st.st_size,
                st.st_mtime_ns, st.st_ctime_ns
            ))
        except FileNotFoundError:
            result.append((suffix, None))
    return result


@contextlib.contextmanager
def readonly_database(filename):
    source = Path(filename).expanduser().resolve()
    if not source.is_file():
        raise RuntimeError('飞牛数据库不存在：' + str(source))

    with tempfile.TemporaryDirectory(prefix='fntv-check-') as directory:
        snapshot = Path(directory) / source.name
        live = None
        try:
            live = sqlite3.connect(
                source.as_uri() + '?mode=ro',
                uri=True, timeout=10
            )
            live.execute('PRAGMA query_only=ON')
            live.execute(
                'SELECT name FROM sqlite_master LIMIT 1'
            ).fetchone()
            target = sqlite3.connect(snapshot)
            try:
                live.backup(target)
            finally:
                target.close()
        except sqlite3.OperationalError:
            if live is not None:
                live.close()
                live = None

            # 只读挂载无法创建 shm 时，在临时目录读取稳定快照。
            print('只读挂载无法直接打开，正在读取临时数据库快照……')
            for attempt in range(3):
                for suffix in ('', '-wal', '-shm', '-journal'):
                    Path(str(snapshot) + suffix).unlink(missing_ok=True)
                before = fingerprint(source)
                try:
                    for row in before:
                        if row[1] is not None:
                            shutil.copyfile(
                                str(source) + row[0],
                                str(snapshot) + row[0]
                            )
                    if before == fingerprint(source):
                        break
                except FileNotFoundError:
                    pass
                if attempt == 2:
                    raise RuntimeError(
                        '飞牛数据库持续写入，无法获得稳定快照；请稍后重试'
                    )
                time.sleep(1)
        finally:
            if live is not None:
                live.close()

        connection = sqlite3.connect(
            snapshot.as_uri() + '?mode=ro',
            uri=True, timeout=10
        )
        try:
            connection.execute('PRAGMA query_only=ON')
            check = connection.execute('PRAGMA quick_check').fetchall()
            if check != [('ok',)]:
                raise RuntimeError('数据库快照校验失败，已停止对比')
            yield connection
        finally:
            connection.close()


def load_fnos(connection):
    needed = {
        'item': {'guid', 'title', 'type', 'tmdb_id', 'parent_guid', 'dir'},
        'item_media': {'item_guid', 'dir', 'path'}
    }
    for table, columns in needed.items():
        actual = {
            row[1]
            for row in connection.execute('PRAGMA table_info(' + table + ')')
        }
        if not columns <= actual:
            raise RuntimeError('飞牛表结构不兼容：' + table)

    items = {
        row[0]: dict(zip(
            ('id', 'name', 'source_type', 'tmdb', 'parent', 'dir'), row
        ))
        for row in connection.execute(
            'SELECT guid,CAST(title AS BLOB),type,tmdb_id,parent_guid,dir '
            'FROM item'
        )
    }

    records = {}
    for guid, item in items.items():
        if item['source_type'] not in ('Movie', 'TV', 'Series'):
            continue
        if guid.startswith('fv_'):
            continue

        kind = 'Movie' if item['source_type'] == 'Movie' else 'Series'
        name = item['name']
        if isinstance(name, bytes):
            name = name.decode('utf-8', 'replace')
        records[guid] = {
            'id': guid,
            'name': name or '',
            'type': kind,
            'tmdb': tmdb_id(item['tmdb']),
            'paths': set(),
            'display_path': item['dir'] or ''
        }
        if item['dir']:
            records[guid]['paths'].add(normalize_path(item['dir']))

    owner_cache = {}

    def owner(guid):
        if guid in owner_cache:
            return owner_cache[guid]
        original, visited = guid, set()
        while guid in items and guid not in visited:
            if guid in records:
                owner_cache[original] = guid
                return guid
            visited.add(guid)
            guid = items[guid]['parent']
        owner_cache[original] = None
        return None

    selected_files = set()
    for guid, directory, path in connection.execute(
        'SELECT item_guid,dir,path FROM item_media'
    ):
        target = owner(guid)
        if target is None:
            continue
        record = records[target]
        # 日志保留数据库原始值；匹配仍使用下面的完整路径集合。
        if record['type'] == 'Movie' and path:
            if target not in selected_files:
                record['display_path'] = path
                selected_files.add(target)
        elif not record['display_path']:
            record['display_path'] = directory or path or ''
        if record['type'] == 'Movie':
            record['paths'].update(
                normalize_path(p) for p in (directory, path) if p
            )
        else:
            directory = directory or (
                posixpath.dirname(normalize_path(path)) if path else ''
            )
            if directory:
                record['paths'].add(series_root(directory))

    return [
        {**record, 'paths': sorted(record['paths'])}
        for record in records.values()
    ]


def library_name(value):
    return unicodedata.normalize('NFC', str(value or '')).strip()


def load_fnos_libraries(connection, records):
    columns = {
        r[1] for r in connection.execute('PRAGMA table_info(item_ancestor)')
    }
    if not {'item_guid', 'ancestor_guid'} <= columns:
        raise RuntimeError('飞牛缺少 item_ancestor 媒体库关联字段')

    # 实际库目录在 mediadb_config.dir_list；item.dir 可能是旧值或只记录一个目录。
    roots_by_id = {}
    config_columns = {r[1] for r in connection.execute('PRAGMA table_info(mediadb_config)')}
    if {'item_guid', 'dir_list'} <= config_columns:
        for guid, value in connection.execute('SELECT item_guid,dir_list FROM mediadb_config'):
            try:
                roots = json.loads(value or '[]')
            except (ValueError, TypeError):
                roots = []
            if isinstance(roots, list):
                roots_by_id[guid] = [normalize_path(root) for root in roots
                                     if isinstance(root, str) and root.strip()]

    libraries = []
    for guid, title, directory in connection.execute(
        "SELECT guid,CAST(title AS BLOB),dir FROM item WHERE type='MediaDB'"
    ):
        name = (
            title.decode('utf-8', 'replace')
            if isinstance(title, bytes) else str(title or '')
        )
        # 飞牛将多个库目录用逗号分隔；只在下一段为绝对路径时拆分。
        roots = roots_by_id.get(guid) or re.split(r',+\s*(?=/)', str(directory or '').strip())
        libraries.append({
            'id': guid, 'name': name, 'records': [],
            'roots': [normalize_path(root) for root in roots if root.strip()]
        })

    by_id = {r['id']: r for r in records}
    members = collections.defaultdict(set)
    query = """
        SELECT a.item_guid,a.ancestor_guid
        FROM item_ancestor a
        JOIN item l ON l.guid=a.ancestor_guid
        WHERE l.type='MediaDB'
    """
    for guid, library in connection.execute(query):
        if guid in by_id:
            members[library].add(guid)

    assigned = set()
    for library in libraries:
        ids = members[library['id']]
        library['records'] = [by_id[guid] for guid in sorted(ids)]
        assigned.update(ids)

    unassigned = [r for r in records if r['id'] not in assigned]
    return libraries, unassigned


def emby_base():
    if not EMBY_BASE_URL or not EMBY_API_KEY:
        raise RuntimeError('请配置青龙环境变量 EMBY_URL 和 EMBY_API_KEY')
    base = EMBY_BASE_URL.rstrip('/')
    if urlsplit(base).scheme.lower() not in ('http', 'https') or not urlsplit(base).netloc:
        raise RuntimeError('EMBY_URL 请填写 HTTP 或 HTTPS 地址，例如 http://192.168.1.2:8096 或 https://emby.example.com')
    return base + '/emby'


def fetch_pages(endpoint, params=None, library_list=False):
    """读取完整分页，并兼容旧版媒体库列表接口。"""
    base = emby_base()
    output, seen, start, expected = [], set(), 0, None
    with requests.Session() as session:
        session.trust_env = False
        session.headers['X-Emby-Token'] = EMBY_API_KEY

        while True:
            query = dict(params or {}, StartIndex=start, Limit=PAGE_SIZE)
            try:
                response = session.get(
                    base + endpoint,
                    params=query,
                    timeout=(10, 90),
                    allow_redirects=False
                )
                if (
                    library_list and start == 0
                    and endpoint.endswith('/Query')
                    and response.status_code in (404, 405)
                ):
                    endpoint = '/Library/VirtualFolders'
                    continue

                if response.status_code != 200:
                    raise RuntimeError(
                        'Emby 返回 HTTP ' + str(response.status_code)
                    )
                payload = response.json()
            except requests.RequestException as error:
                raise RuntimeError(
                    'Emby 读取失败：' + type(error).__name__
                ) from None
            except ValueError:
                raise RuntimeError('Emby 返回的不是有效 JSON') from None

            if library_list and isinstance(payload, list):
                batch, total = payload, len(payload)
            elif (
                isinstance(payload, dict)
                and isinstance(payload.get('Items'), list)
            ):
                batch, total = payload['Items'], payload.get('TotalRecordCount')
            else:
                raise RuntimeError('Emby 数据结构异常')

            if (
                not isinstance(total, int) or total < 0
                or (expected is not None and total != expected)
            ):
                raise RuntimeError('Emby 总数无效或分页期间发生变化，请重试')
            expected = total

            if not batch and start < total:
                raise RuntimeError('Emby 分页提前结束')

            for item in batch:
                identity = str(
                    (item.get('ItemId') if library_list else None)
                    or item.get('Id') or ''
                )
                if not identity or identity in seen:
                    raise RuntimeError('Emby 返回重复或缺失条目 ID')
                seen.add(identity)
                output.append(item)

            start += len(batch)
            if start >= total:
                if len(seen) != total:
                    raise RuntimeError('Emby 分页总数不一致')
                return output


def fetch_emby_libraries():
    rows = fetch_pages('/Library/VirtualFolders/Query', library_list=True)
    return [
        {
            'id': str(r.get('ItemId') or r.get('Id')),
            'name': r.get('Name') or '',
            'roots': [normalize_path(p) for p in r.get('Locations') or [] if p]
        }
        for r in rows
    ]


def fetch_emby(library):
    rows = fetch_pages('/Items', {
        'ParentId': library['id'],
        'Recursive': 'true',
        'IncludeItemTypes': 'Movie,Series',
        'Fields': 'ProviderIds,Path,MediaSources',
        'SortBy': 'SortName',
        'SortOrder': 'Ascending'
    })
    records = []
    for item in rows:
        if item.get('Type') not in ('Movie', 'Series'):
            raise RuntimeError('Emby 返回了非电影/整部剧集条目')

        paths = {normalize_path(item.get('Path'))}
        paths.update(
            normalize_path(m.get('Path'))
            for m in item.get('MediaSources') or []
        )
        paths.discard('')
        providers = {
            k.lower(): v for k, v in (item.get('ProviderIds') or {}).items()
        }
        records.append({
            'id': str(item['Id']),
            'name': item.get('Name') or '',
            'type': item['Type'],
            'tmdb': tmdb_id(providers.get('tmdb')),
            'paths': sorted(paths),
            'item_path': normalize_path(item.get('Path')),
            'source_paths': [normalize_path(source['Path'])
                             for source in item.get('MediaSources') or []
                             if source.get('Path')],
            # 日志优先显示媒体源中的实际视频路径，跳过 .strm。
            'display_path': next(
                (source['Path'] for source in item.get('MediaSources') or []
                 if source.get('Path')
                 and posixpath.splitext(source['Path'])[1].lower() != '.strm'),
                item.get('Path') or ''
                if posixpath.splitext(item.get('Path') or '')[1].lower() != '.strm'
                else ''
            )
        })
    return records


def pair_libraries(fnos, emby):
    def index(libraries):
        result = collections.defaultdict(list)
        for library in libraries:
            name = library_name(library['name'])
            if not name:
                raise RuntimeError('媒体库名称为空，无法按名称匹配')
            result[name].append(library)
        return result

    fi, ei = index(fnos), index(emby)
    pairs = []
    for name in sorted(fi.keys() & ei.keys()):
        if len(fi[name]) != 1 or len(ei[name]) != 1:
            raise RuntimeError('存在同名重复媒体库，无法唯一匹配：' + name)
        pairs.append((fi[name][0], ei[name][0]))

    skipped = {
        'fnos_only': sorted(fi.keys() - ei.keys()),
        'emby_only': sorted(ei.keys() - fi.keys())
    }
    return pairs, skipped


def path_aliases(record):
    paths = set(record['paths'])
    if record['type'] == 'Movie':
        extensions = (
            '.mkv', '.mp4', '.avi', '.mov', '.m2ts',
            '.ts', '.iso', '.strm', '.wmv'
        )
        for path in record['paths']:
            if posixpath.splitext(path)[1].lower() in extensions:
                paths.add(posixpath.dirname(path))
    return {path for path in paths if path and path != '/'}


def under_root(path, root):
    """按目录边界取相对路径，不做字符串全局替换。"""
    path, root = normalize_path(path), normalize_path(root)
    if not root or root == '/' or not path.startswith('/'):
        return None
    if path == root:
        return '.'
    return path[len(root) + 1:] if path.startswith(root + '/') else None


def library_base(library):
    roots = [r for r in library.get('roots', []) if r.startswith('/')]
    if not roots:
        return ''
    base = posixpath.commonpath(roots)
    return base if base != '/' else ''


def prepare_paths(f_library, e_library, emby):
    """配置决定库的根目录；STRM 与媒体源的对应关系决定实际视频挂载前缀。"""
    f_base, e_base = library_base(f_library), library_base(e_library)
    # 一个库配置多目录时，以这些目录的共同父目录保留子目录区别。
    e_bases = {e_base} if e_base else set()
    # 根目录的目录名也从配置取得：在实际源路径里寻找最长对应的目录尾部。
    # 例如配置在 STRM 目录、实际源在另一块挂载盘，仍可自动找到实际库根目录。
    reference = f_base.strip('/').split('/') if f_base else []
    for record in emby:
        for path in record['paths']:
            if not path.startswith('/'):
                continue
            directory = posixpath.dirname(path) if record['type'] == 'Movie' else path
            parts = directory.strip('/').split('/')
            for size in range(len(reference), 0, -1):
                suffix = reference[-size:]
                hits = [index for index in range(len(parts) - size + 1)
                        if parts[index:index + size] == suffix]
                if hits:
                    if len(hits) == 1:
                        e_bases.add('/' + '/'.join(parts[:hits[0] + size]))
                    break
    for record in emby:
        relative = under_root(record.get('item_path', ''), e_base)
        if relative is None:
            continue
        directory = posixpath.dirname(relative)
        for source in record.get('source_paths', []):
            if not source.startswith('/'):
                continue
            parent = posixpath.dirname(source)
            if not directory:
                e_bases.add(parent)
            elif parent.endswith('/' + directory):
                e_bases.add(parent[:-len(directory) - 1])

    def attach(records, bases):
        result = []
        for record in records:
            aliases = {}
            for path in path_aliases(record):
                # 嵌套库目录选最长的目录前缀，避免 Movies 与 Movies2 混淆。
                matches = [(len(base), relative) for base in bases
                           if (relative := under_root(path, base)) is not None]
                if matches:
                    aliases[path] = max(matches)[1]
            raw = normalize_path(record.get('display_path', ''))
            result.append({**record, 'relative_paths': aliases,
                           'short_path': aliases.get(raw, record.get('display_path', ''))})
        return result

    return attach(f_library['records'], {f_base} if f_base else set()), attach(emby, e_bases)


def actual_paths(record):
    if record['type'] == 'Series':
        return set(record['paths'])
    return {p for p in record['paths']
            if posixpath.splitext(p)[1].lower() in (
                '.mkv', '.mp4', '.avi', '.mov', '.m2ts', '.ts', '.iso', '.wmv',
                '.m4v', '.webm', '.mpg', '.mpeg', '.flv', '.vob'
            )}


def compare(fnos, emby):
    summary, only_ids, missing = {}, {}, {}
    for kind in ('Movie', 'Series'):
        f = [r for r in fnos if r['type'] == kind]
        e = [r for r in emby if r['type'] == kind]
        fi = {r['tmdb'] for r in f if r['tmdb']}
        ei = {r['tmdb'] for r in e if r['tmdb']}

        summary[kind] = {
            'fnos_items': len(f),
            'emby_items': len(e),
            'fnos_unique_ids': len(fi),
            'emby_unique_ids': len(ei),
            'fnos_extra_versions': sum(bool(r['tmdb']) for r in f) - len(fi),
            'emby_extra_versions': sum(bool(r['tmdb']) for r in e) - len(ei),
            'shared_ids': len(fi & ei),
            'fnos_only_ids': len(fi - ei),
            'emby_only_ids': len(ei - fi)
        }
        only_ids[kind] = {
            'fnos': [r for r in f if r['tmdb'] in fi - ei],
            'emby': [r for r in e if r['tmdb'] in ei - fi]
        }
        missing[kind] = {
            'fnos': [r for r in f if not r['tmdb']],
            'emby': [r for r in e if not r['tmdb']]
        }

    exact, relative, folders = (collections.defaultdict(set) for _ in range(3))
    for index, record in enumerate(fnos):
        for path in actual_paths(record):
            exact[(record['type'], path)].add(index)
            rel = record.get('relative_paths', {}).get(path)
            if rel is not None:
                relative[(record['type'], rel)].add(index)
        for path in path_aliases(record) - actual_paths(record):
            rel = record.get('relative_paths', {}).get(path)
            if rel is not None:
                folders[(record['type'], rel)].add(index)

    mismatches, candidates, unresolved = [], [], []
    matched = 0
    for record in emby:
        hits = set()
        for path in actual_paths(record):
            hits.update(exact[(record['type'], path)])
        method = '同一路径'

        if not hits:
            for path in actual_paths(record):
                rel = record.get('relative_paths', {}).get(path)
                if rel is not None:
                    hits.update(relative[(record['type'], rel)])
            method = '库内相对路径一致'

        if not hits and record['type'] == 'Movie':
            for path in path_aliases(record) - actual_paths(record):
                rel = record.get('relative_paths', {}).get(path)
                if rel is not None:
                    hits.update(folders[(record['type'], rel)])
            method = '库内目录一致（文件尚未核实）'

        if len(hits) != 1:
            unresolved.append({
                'emby': record,
                'reason': '路径有多个候选' if hits else '未找到相同路径',
                'fnos_candidates': [fnos[i] for i in sorted(hits)]
            })
            continue

        other = fnos[next(iter(hits))]
        equal = bool(record['tmdb']) and record['tmdb'] == other['tmdb']
        entry = {
            'type': record['type'],
            'method': method,
            'emby': record,
            'fnos': other,
            'same_tmdb': equal
        }
        if method == '库内目录一致（文件尚未核实）':
            candidates.append(entry)
        else:
            matched += 1
            if not equal:
                mismatches.append(entry)

    remaining_ids = {
        kind: {side: len(
            {r['tmdb'] for r in only_ids[kind][side]}
            - {p[side]['tmdb'] for p in mismatches if p['type'] == kind}
        ) for side in ('fnos', 'emby')}
        for kind in ('Movie', 'Series')
    }
    return {
        'summary': summary,
        'remaining_ids': remaining_ids,
        'only_ids': only_ids,
        'missing_ids': missing,
        'exact_path_compared': matched,
        'path_mismatches': mismatches,
        'relative_path_candidates': candidates,
        'unmatched_paths': unresolved
    }


def log_path(record):
    return record.get('short_path', record.get('display_path', next(iter(record['paths']), '')))


def show_record(record, show_path=True):
    print(
        ' ', record['name'],
        '| TMDB:', record['tmdb'] or '未识别',
        '| 条目:', record['id']
    )
    if show_path:
        print('    路径：', log_path(record) or '无路径')


def show_pair(pair):
    f_path, e_path = log_path(pair['fnos']), log_path(pair['emby'])
    same_path = bool(f_path) and normalize_path(f_path) == normalize_path(e_path)
    print('Emby:')
    show_record(pair['emby'], show_path=not same_path)
    print('飞牛:')
    show_record(pair['fnos'], show_path=not same_path)
    if same_path:
        print('  共同路径：', e_path)


def show_library_report(name, report, show_header=True):
    different = [p for p in report['relative_path_candidates'] if not p['same_tmdb']]
    if (
        not report['path_mismatches'] and not different
        and not report['unmatched_paths']
        and all(
            not data['fnos_only_ids'] and not data['emby_only_ids']
            and not report['missing_ids'][kind]['fnos']
            and not report['missing_ids'][kind]['emby']
            for kind, data in report['summary'].items()
        )
    ):
        counts = '；'.join(
            str(report['summary'][kind]['shared_ids']) + '（' + label + '）'
            for kind, label in (('Movie', '电影'), ('Series', '整部剧集'))
            if report['summary'][kind]['fnos_items'] or report['summary'][kind]['emby_items']
        )
        print('✅ ' + name + '：共同 TMDB ID ' + (counts or '0') + '，无 ID 集合差异', flush=True)
        return
    if show_header:
        issues = []
        if report['path_mismatches']:
            issues.append('同路径 ID 差异 ' + str(len(report['path_mismatches'])) + ' 处')
        if different:
            issues.append('目录候选 ID 差异 ' + str(len(different)) + ' 处（文件未核实）')
        for side, side_name in (('fnos', '飞牛'), ('emby', 'Emby')):
            remaining = sum(report['remaining_ids'][kind][side] for kind in ('Movie', 'Series'))
            if remaining:
                issues.append('排除同路径差异后 ' + side_name + ' 独有 ID ' + str(remaining) + ' 个')
            missing = sum(len(report['missing_ids'][kind][side]) for kind in ('Movie', 'Series'))
            if missing:
                issues.append(side_name + ' 未识别 ' + str(missing) + ' 个条目')
        if not issues:
            issues.append('路径未匹配或候选不唯一 ' + str(len(report['unmatched_paths'])) + ' 个条目')
        print('⚠️ ' + name + '：' + '，'.join(issues), flush=True)
    paired_ids = {
        side: {p[side]['id'] for p in report['path_mismatches'] + different}
        for side in ('fnos', 'emby')
    }
    for kind, label in (('Movie', '电影'), ('Series', '整部剧集')):
        data = report['summary'][kind]
        if not data['fnos_items'] and not data['emby_items']:
            continue
        print(
            label + '（按 TMDB ID 去重）：飞牛 ' + str(data['fnos_unique_ids'])
            + '，Emby ' + str(data['emby_unique_ids'])
            + '；共同 TMDB ID ' + str(data['shared_ids'])
        )
        if data['fnos_only_ids'] or data['emby_only_ids']:
            print(
                '  原始 ID 差集（含单独列出的路径差异及目录候选）：飞牛 '
                + str(data['fnos_only_ids']) + '；Emby ' + str(data['emby_only_ids'])
            )
        for side, side_name in (('fnos', '飞牛'), ('emby', 'Emby')):
            for field, title in (
                ('only_ids', '独有 TMDB ID（路径差异及目录候选另列）'),
                ('missing_ids', '未识别')
            ):
                rows = [r for r in report[field][kind][side]
                        if r['id'] not in paired_ids[side]]
                if rows:
                    print(side_name + label + title + '：')
                    for row in sorted(
                        rows, key=lambda r: (r['tmdb'], r['name'])
                    ):
                        show_record(row)

    if report['path_mismatches']:
        print('文件/剧集目录路径一致，但 TMDB ID 不同或缺失：', len(report['path_mismatches']))
    for pair in report['path_mismatches']:
        print('匹配方式：', pair['method'])
        show_pair(pair)

    if different:
        print('库内目录一致、ID 不同或缺失的候选（文件未核实）：', len(different))
    for pair in different:
        print('候选（请确认是同一份媒体）：')
        show_pair(pair)

    if report['unmatched_paths']:
        # 单边 ID、未识别等上面已经列过；这里只补充还没展示的条目。
        shown = {
            r['id'] for field in ('only_ids', 'missing_ids')
            for kind in ('Movie', 'Series') for r in report[field][kind]['emby']
        }
        for entry in report['unmatched_paths']:
            if entry['emby']['id'] not in shown:
                print('Emby：', entry['reason'])
                show_record(entry['emby'])


def main():
    with readonly_database(FNTV_DB) as connection:
        records = load_fnos(connection)
        fnos, unassigned = load_fnos_libraries(connection, records)

    emby = fetch_emby_libraries()
    pairs, skipped = pair_libraries(fnos, emby)
    print('同名媒体库（' + str(len(pairs)) + ' 个）：')
    print('、'.join(f['name'] for f, e in pairs) or '无', flush=True)
    print('跳过 飞牛 独有库：', '、'.join(skipped['fnos_only']) or '无')
    print('跳过 Emby 独有库：', '、'.join(skipped['emby_only']) or '无')

    if not pairs:
        raise RuntimeError('两边没有同名媒体库，未进行对比')
    print('\n========== 开始对比 ==========', flush=True)
    reports = []
    for f, e in pairs:
        f_records, e_records = prepare_paths(f, e, fetch_emby(e))
        if not library_base(f) or not library_base(e):
            print(f['name'] + '：库目录未提供或没有可用的共同根目录，无法进行库内相对路径匹配。')
        report = compare(f_records, e_records)
        reports.append({
            'name': f['name'],
            'fnos_library_id': f['id'],
            'emby_library_id': e['id'],
            'result': report
        })
        show_library_report(f['name'], report)

    print('\n========== 最终汇总 ==========')
    print('已对比同名媒体库：', len(reports), '个（以下数量按库累计）')
    for kind, label in (('Movie', '电影'), ('Series', '整部剧集')):
        rows = [r['result']['summary'][kind] for r in reports]
        print(
            label + '（按 TMDB ID 去重）：飞牛', sum(r['fnos_unique_ids'] for r in rows),
            '；Emby', sum(r['emby_unique_ids'] for r in rows),
            '；共同 TMDB ID', sum(r['shared_ids'] for r in rows)
        )
        raw_ids = [sum(r[side + '_only_ids'] for r in rows) for side in ('fnos', 'emby')]
        if any(raw_ids):
            print('  原始 ID 差集（含路径差异及目录候选，飞牛/Emby）：', '/'.join(map(str, raw_ids)))
        missing_counts = [sum(
            len(r['result']['missing_ids'][kind][side]) for r in reports
        ) for side in ('fnos', 'emby')]
        if any(missing_counts):
            print('  ⚠️ 未识别（飞牛/Emby）：', '/'.join(map(str, missing_counts)))
    mismatch_count = sum(
        len(r['result']['path_mismatches']) for r in reports
    )
    if mismatch_count:
        print('⚠️ 文件/剧集目录路径一致，但 TMDB ID 不同或缺失：', mismatch_count)
    candidate_count = sum(
        sum(not pair['same_tmdb'] for pair in r['result']['relative_path_candidates'])
        for r in reports
    )
    if candidate_count:
        print('⚠️ 目录候选 ID 不同或缺失（文件未核实）：', candidate_count)
    remaining_counts = [sum(
        r['result']['remaining_ids'][kind][side]
        for r in reports for kind in ('Movie', 'Series')
    ) for side in ('fnos', 'emby')]
    if any(remaining_counts):
        print('📋 排除同路径 ID 差异条目后的独有 ID（飞牛/Emby）：', '/'.join(map(str, remaining_counts)))
    if unassigned:
        print('⚠️ 飞牛无媒体库关联的电影/剧集：', len(unassigned), '（未加入比较）')
    print('ID 差异不代表已确认识别错误，请人工核查。')
    print('单边 ID 可能是未入库或扫描范围不同；未能对应的条目尚未核实。')



if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, sqlite3.Error) as error:
        print('检查失败：', error, file=sys.stderr)
        sys.exit(1)
