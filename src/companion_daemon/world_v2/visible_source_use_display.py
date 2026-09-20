"""Expose existing evidence uses without selecting evidence or judging prose.

Permissions and facts come from the unchanged compiler. These descriptions are
only a model-facing explanation of that contract, never source material.
"""
from .shared_string_view import pack_shared_strings, unpack_shared_strings

CONTRACT = 'visible-source-use-display.1'
SCOPE_MEANINGS = {
    'utterance_record': '仅支持该主体此前说过所记内容；不证明话中经历实际发生。',
    'report_uptake': (
        '自然承接用户报告的处境，包括以其为前提的问句；不要求独立客观证明或额外“你说”归因语。'
        '必须保留报告的主体、受事、时间、肯否、事件状态及细节；不支持角色经历或客观世界写入。'),
    'accepted_intention': '仅支持所记主体的已接受意图；不证明行动、完成或意图内嵌的经历。',
    'activity_lifecycle': '仅支持同一活动字段明确记录的生命周期状态及时间；不证明意图实现或执行成功。',
    'environment': '仅支持正文所记环境事实；不证明角色在场、参与或行动。',
    'external_fact': '核对正文记录的完整外部命题，不能从来源持有者身份推导行为者。',
    'subjective_history': '仅支持所记角色当时的主观历史；不证明外部事件或其他时刻状态。',
    'accepted_fact': '仅按绑定主体、谓词和有效时间核对精确接受值；观察背景不是接受值。',
    'historical_accepted_fact': '仅按绑定主体、谓词和历史有效时间核对精确接受值；不等于当前事实。',
}
SUPPORT_DESCRIPTION = (
    '在 fixed_fact 的 eligible_source_uses 指定用途下，所选正文是否支持完整原命题。'
    'report_uptake 核对是否忠实承接用户报告，无需独立证明报告的事情客观发生；'
    'utterance_record 只证明说过，不能证明实际经历。用途合格不等于语义匹配；'
    '仍须逐项核对主体、受事、时间、肯否、状态与细节，不改写 fixed_fact 取得支持。')


def configure_source_use_display(*, body, fact_item, eligible_uses):
    """Add descriptions of exactly the already-compiled permissions.

    The original pinned catalog is unchanged. Unknown scopes fail compilation
    instead of receiving a generic explanation that might broaden authority.
    """
    materials = unpack_shared_strings(body['source_materials'])
    for material in materials:
        for reading in material['readings']:
            permissions = [*reading['allowed_claims'], *reading.get('value_selection_permissions', [])]
            scopes = sorted({scope for scope, _ in permissions})
            reading['claim_scope_meanings'] = {scope: SCOPE_MEANINGS[scope] for scope in scopes}
    body['source_materials'] = pack_shared_strings(materials)
    for fact in body['fixed_facts']:
        uses = eligible_uses[fact['fact_id']]
        if set(uses) != set(fact['eligible_reading_ids'] + fact.get('eligible_fact_value_ids', [])):
            raise ValueError('source use display must preserve every existing eligible reading')
        fact['eligible_source_uses'] = dict(uses)
    body['source_use_display_contract'] = CONTRACT
    body['source_support_contract'] = SUPPORT_DESCRIPTION
    fact_item['properties']['source_support']['description'] = SUPPORT_DESCRIPTION
