import json
import pytest
from companion_daemon.config import Settings
from companion_daemon.world_v2.prehistory_deployment import configured_prehistory
from test_character_prehistory import reviewed_archive, WORLD_ID, ACTOR


def test_reviewed_package_remains_opt_in_and_binds_exact_owner(tmp_path):
    assert Settings(_env_file=None).world_v2_prehistory_package_path is None
    args=dict(supplied=None,world_id=WORLD_ID,actor_ref=ACTOR)
    assert configured_prehistory(path=None,**args) is None
    path=tmp_path/'reviewed.json';package=reviewed_archive();path.write_text(package.model_dump_json())
    assert configured_prehistory(path=path,**args)==package
    with pytest.raises(ValueError,match='another World'):
        configured_prehistory(path=path,**{**args,'world_id':'world:other'})
    with pytest.raises(ValueError,match='another World'):
        configured_prehistory(path=path,**{**args,'actor_ref':'actor:other'})
    raw=package.model_dump(mode='json');raw['document']['records'][0]['statement']='Unreviewed replacement'
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError,match='exact document'):
        configured_prehistory(path=path,**args)


def test_draft_without_review_and_oversized_input_are_not_importable(tmp_path):
    path=tmp_path/'input.json';args=dict(supplied=None,world_id=WORLD_ID,actor_ref=ACTOR)
    path.write_text(reviewed_archive().document.model_dump_json())
    with pytest.raises(ValueError):configured_prehistory(path=path,**args)
    path.write_bytes(b' '*2_000_001)
    with pytest.raises(ValueError,match='size limit'):configured_prehistory(path=path,**args)
