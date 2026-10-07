import pytest
from companion_daemon.world_v2.story_source_plan import ManuscriptPlan, plan_manuscript


def test_unicode_manuscript_partition_covers_all_bytes_in_original_order():
    text = '第一条线\r\n' + ('她把旧信放回抽屉。\n\n' * 50) + '第二条线\n' + ('🙂最后一段没有换行' * 40)
    plan = plan_manuscript(text, chapter_bytes_limit=512)
    assert len(plan.chapters) > 2
    assert ''.join(chapter.text for chapter in plan.chapters) == text
    assert plan == plan_manuscript(text, chapter_bytes_limit=512)
    assert all(len(chapter.text.encode('utf-8')) <= 512 for chapter in plan.chapters)
    corrupt = plan.model_copy(update={'chapters': plan.chapters[:-1]})
    with pytest.raises(ValueError, match='coverage'):
        ManuscriptPlan.model_validate_json(corrupt.model_dump_json())
