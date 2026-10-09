"""sweep_legacy: retired driver/engine artifacts are swept; author and live files survive."""
from pathlib import Path

from tests.support import contract

from novel_core import engine
from novel_core.store import initialize


def plant(project):
    (project/'st3.err').write_text('boom', encoding='utf-8')
    (project/'st3.tmp.json').write_text('{}', encoding='utf-8')
    (project/'tmp_context_capacity.json').write_text('{}', encoding='utf-8')
    (project/'tmp_context_anchors.json').write_text('{}', encoding='utf-8')
    (project/'workch0034facts-76205bc3_ch34_recall.json').write_text('{}', encoding='utf-8')
    (project/'novel.db').write_text('', encoding='utf-8')
    (project/'_review_tmp').mkdir()
    (project/'_review_tmp'/'probe.json').write_text('{}', encoding='utf-8')
    work = project/'work'
    work.mkdir(exist_ok=True)
    (work/'_tmp_read.json').write_text('{}', encoding='utf-8')
    (work/'_tmp_chars.md').write_text('x', encoding='utf-8')
    (work/'next_task.json').write_text('{}', encoding='utf-8')
    (work/'plans_data.json').write_text('[]', encoding='utf-8')
    (work/'vol_data.json').write_text('[]', encoding='utf-8')
    (work/'_review_tmp').mkdir()
    (work/'ch0001').mkdir()
    (work/'ch0001'/'draft-abc').mkdir()
    (work/'ch0001'/'draft-abc'/'task.json').write_text('{}', encoding='utf-8')
    (project/'contract.json').write_text('{}', encoding='utf-8')
    (project/'book.html').write_text('<html></html>', encoding='utf-8')
    (project/'export').mkdir(exist_ok=True)
    (project/'export'/'0001.md').write_text('正文', encoding='utf-8')
    (project/'旧稿存档').mkdir(exist_ok=True)
    (project/'旧稿存档'/'第一章.md').write_text('旧稿', encoding='utf-8')


LITTER = {'st3.err', 'st3.tmp.json', 'tmp_context_capacity.json', 'tmp_context_anchors.json',
          'workch0034facts-76205bc3_ch34_recall.json', 'novel.db', '_review_tmp/',
          'work/_tmp_read.json', 'work/_tmp_chars.md', 'work/next_task.json',
          'work/plans_data.json', 'work/vol_data.json', 'work/_review_tmp/'}


def test_sweep_removes_legacy_litter_and_spares_author_files(tmp_path):
    project = tmp_path/'p'
    initialize(project, contract())
    plant(project)
    report = engine.sweep_legacy(project)
    assert set(report['removed']) == LITTER
    assert report['kept'] == []
    for rel in ('contract.json', 'book.html', 'novel.sqlite3', 'export/0001.md',
                '旧稿存档/第一章.md', 'work/ch0001/draft-abc/task.json'):
        assert (project/rel).exists(), rel


def test_dry_run_lists_without_deleting(tmp_path):
    project = tmp_path/'p'
    initialize(project, contract())
    plant(project)
    report = engine.sweep_legacy(project, apply=False)
    assert set(report['removed']) == LITTER
    assert (project/'st3.err').exists()
    assert (project/'work'/'next_task.json').exists()
    assert (project/'_review_tmp').is_dir()


def test_nonempty_legacy_db_is_kept_and_reported(tmp_path):
    project = tmp_path/'p'
    initialize(project, contract())
    (project/'novel.db').write_text('real data', encoding='utf-8')
    report = engine.sweep_legacy(project)
    assert report['removed'] == []
    assert report['kept'] == [{'path': 'novel.db', 'reason': '非空旧库,请人工确认后再处理'}]
    assert (project/'novel.db').exists()


def test_sweep_on_clean_project_is_noop(tmp_path):
    project = tmp_path/'p'
    initialize(project, contract())
    report = engine.sweep_legacy(project)
    assert report == {'removed': [], 'kept': []}
