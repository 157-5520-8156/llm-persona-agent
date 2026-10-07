"""Make the candidate's knowledge grants explicit to offline reviewers."""

ACTOR_READABLE_REVIEW_INSTRUCTION = (
    '候选不是作者的世界设定草稿：documents.records全部是准备提供给source_owner读取的个人历史。'
    'actor_readable_records明确列出这些知识授予；其中statement及blocks.text都会成为该actor可用内容，'
    '不存在“保留在作者视角但不给角色看”的隐藏通道。未写“我知道”也不取消知识授予。'
    '原始sources中的旁白可以成立，但若缺乏该actor的知情依据，不能因此进入actor_readable_records。'
    '档案import_context中的既有快照只作审核证据，不是本次新增的角色读取记录。'
)


def actor_readable_history(documents):
    return [{'record_ref': r.record_id, 'reader_actor_ref': doc.actor_ref,
        'statement': r.statement, 'blocks': [dict(block_id=b.block_id, text=b.text, kind=b.kind,
            subject_actor_ref=b.subject_actor_ref) for b in r.story.blocks] if r.story else [],
        'acquisition_from': r.story.known_from.isoformat() if r.story else None,
        'acquisition_until': r.story.known_until.isoformat() if r.story else None,
    } for doc in documents for r in doc.records]
