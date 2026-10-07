"""Human-readable source and coverage; never a character prompt or new authority."""
from __future__ import annotations

from collections import Counter

from .character_prehistory import digest


def native_life_catalogue(source, brief, package):
    cutoff = source.knowledge_cutoff or brief.world_started_at
    legacy = [r for a in brief.accepted_archives for r in a.records]
    core = [r for r in legacy if r.occurred_until <= cutoff]
    crossing = [r for r in legacy if r.occurred_from <= cutoff < r.occurred_until]
    later = [r for r in legacy if r.occurred_from > cutoff]
    line_names = {line.line_id: line.title for line in source.storylines}
    line_counts = Counter(ref for scene in source.scenes for ref in scene.line_refs)
    blocks = [b for scene in source.scenes for b in scene.blocks]
    stats = {'contract': 'native-life-coverage.1', 'scope': 'source_catalogue_not_production_adoption',
        'source_hash': digest(source), 'compiled_manifest_hash': digest(package.document.manifest()),
        'born_at': brief.born_at.isoformat(), 'cutoff': cutoff.isoformat(),
        'native_scenes': len(source.scenes), 'native_blocks': len(blocks), 'native_records': len(package.document.records),
        'world_only_blocks': sum(b.knowledge == 'world_only' for b in blocks),
        'legacy_records_unchanged': len(legacy), 'legacy_wholly_before_cutoff': len(core),
        'legacy_interval_crosses_cutoff': len(crossing), 'legacy_later_bridge': len(later),
        'native_scene_start_year_counts': dict(sorted(Counter(s.occurred_from.year if s.occurred_from else 'unknown' for s in source.scenes).items(), key=lambda p: str(p[0]))),
        'native_knowledge_kinds': dict(Counter(b.knowledge for b in blocks)),
        'storylines': [{'line_id': key, 'title': title, 'scenes': line_counts[key]} for key, title in line_names.items()],
        'shared_scenes': [{'event_ref': s.event_ref, 'title': s.title, 'line_refs': s.line_refs}
                          for s in source.scenes if len(s.line_refs) > 1],
        'explicit_native_relations': len(source.links), 'legacy_connections': len(source.legacy_links),
        'remaining_open_questions': list(source.open_questions),
        'limits': ['Coverage counts describe declared source structure, not a claim of semantic infallibility.',
                   'Legacy time intervals keep their original precision; crossing intervals are not assigned a guessed date.',
                   'World-only prose and story-line labels are operator source material, not actor-readable memory.']}
    pages = [f'# {source.title}', '',
        f'主体时间：{brief.born_at.date()} 至 {cutoff.date()}。原生创作底稿；角色只能读取被明确授予的部分。',
        f'已有{len(legacy)}条历史的原始内容单独保留在后面的衔接部分；新源稿并不覆盖旧记录。', '',
        '## 本轮原生故事', '']
    for scene in sorted(source.scenes, key=lambda s: (s.occurred_from is None, s.occurred_from or cutoff, s.event_ref)):
        period = f'{scene.occurred_from.date()}—{scene.occurred_until.date()}' if scene.occurred_from else '发生时间未定'
        pages.extend([f'### {period} · {scene.title}', '',
            '故事线：' + ' / '.join(line_names[r] for r in scene.line_refs), f'发生编号：`{scene.event_ref}`', ''])
        for block in scene.blocks:
            if block.knowledge == 'world_only':
                label = '作者世界层；不授予主角记忆'
            elif block.knowledge == 'told':
                label = f'后来由 {block.informant_ref} 讲述'
            elif block.kind == 'historical_interpretation':
                label = '她当时的理解；不规定现在或未来的反应'
            elif block.kind == 'intention':
                label = '当时的意向；不代表已经实行'
            else:
                label = '个人可读经历'
            pages.extend([f'**{block.block_id} · {label}**', '', block.text, ''])
    for title, records in [('原有历史：时间区间全部位于主体范围', core),
                           ('原有历史：日期区间跨过二十岁生日，保持原有不确定性', crossing),
                           ('原有历史：二十岁之后的既有衔接', later)]:
        pages.extend([f'## {title}', ''])
        for record in sorted(records, key=lambda r: (r.occurred_from, r.record_id)):
            pages.extend([f'### {record.record_id}', '', record.statement, ''])
    pages.extend(['## 截至底稿末仍未确定的事情', ''])
    pages.extend('- ' + question for question in source.open_questions)
    return stats, '\n'.join(pages) + '\n'
