"""Operator provisioning for the adult-media eligibility chain.

Adult intensity is not implied by relationship stage or by the ordinary media
enforcement grants.  This module writes one dedicated ``CapabilityGranted``
and one dedicated ``ConsentGranted`` at the ledger head, reusing any already
active actor authority for the operator and the user.  The deployment switch
``WORLD_V2_ADULT_MEDIA_ENABLED`` stays independent and defaults off.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
import logging
from typing import Mapping

from nacl.signing import SigningKey

from .actor_authority_events import (
    ROOT_KEYSET_DIGEST,
    ROOT_KEYSET_VERSION,
    ROOT_PUBLIC_KEYS,
    actor_authority_mutation_hash,
    root_envelope_signature_message,
)
from .actor_authority_reducers import ACTOR_AUTHORITY_POLICY_DIGEST
from .adult_media_authority import (
    ADULT_MEDIA_ACTION_SCOPE,
    ADULT_MEDIA_CAPABILITY_ID,
    ADULT_MEDIA_CAPABILITY_KIND,
    ADULT_MEDIA_CONSENT_ID,
)
from .authorization_events import (
    CAPABILITY_POLICY_DIGEST,
    CONSENT_POLICY_DIGEST,
    ENFORCEMENT_EXTERNAL_PRINCIPAL_AUTH_POLICY_DIGEST,
    authorization_intent_hash,
    authorization_mutation_hash,
    authorization_scope_hash,
)
from .event_identity import domain_idempotency_key
from .media_authority_provisioning import MEDIA_CONTINUATION_ACTOR
from .schemas import WorldEvent


_LOG = logging.getLogger(__name__)

_USER_AUTHORITY_ID = "authority:world-v2:adult-media-user"
_OPERATOR_AUTHORITY_ID = "authority:world-v2:adult-media-operator"


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class AdultMediaAuthorityProvisioningResult:
    committed_event_ids: tuple[str, ...]
    already_present: tuple[str, ...]


class AdultMediaAuthorityProvisioner:
    """Write the adult-media eligibility pair once, idempotently, at the head."""

    def __init__(
        self,
        *,
        ledger,
        signing_key_hex: str,
        subject_ref: str,
        operator_ref: str = "operator:girl-agent",
    ) -> None:
        if not subject_ref or not operator_ref:
            raise ValueError("adult media provisioning requires subject and operator refs")
        try:
            self._signing_key = SigningKey(bytes.fromhex(signing_key_hex.strip()))
        except Exception as exc:
            raise ValueError("deployment root signing key must be a 32-byte hex seed") from exc
        verify_hex = self._signing_key.verify_key.encode().hex()
        self._root_key_id = next(
            (key_id for key_id, public in ROOT_PUBLIC_KEYS.items() if public == verify_hex),
            None,
        )
        if self._root_key_id is None:
            raise ValueError("supplied signing key does not match an installed deployment root")
        self._ledger = ledger
        self._subject_ref = subject_ref
        self._operator_ref = operator_ref

    @staticmethod
    def _active_authority_for(
        projection, *, principal_ref: str, required_operations: tuple[str, ...]
    ) -> str | None:
        """Reuse a sibling chain's authority; a second active one is rejected."""

        for item in projection.actor_authorities:
            if (
                item.values.principal_ref == principal_ref
                and item.values.status == "active"
                and all(
                    operation in item.values.allowed_operations
                    for operation in required_operations
                )
            ):
                return item.authority_id
        return None

    def _authority_revision(self, authority_id: str) -> int:
        projection = self._ledger.project()
        for item in projection.actor_authorities:
            if item.authority_id == authority_id and item.values.status == "active":
                return item.entity_revision
        raise ValueError("adult media actor authority is not active")

    def ensure(self) -> AdultMediaAuthorityProvisioningResult:
        committed: list[str] = []
        present: list[str] = []
        projection = self._ledger.project()
        if projection.logical_time is None:
            raise ValueError("adult media provisioning requires an established world clock")

        resolved_authority_ids: dict[str, str] = {}
        for authority_id, principal, kind, operations in (
            (
                _USER_AUTHORITY_ID,
                self._subject_ref,
                "user_consent_principal",
                ("consent_grant", "privacy_policy"),
            ),
            (
                _OPERATOR_AUTHORITY_ID,
                self._operator_ref,
                "deployment_operator",
                ("capability_grant",),
            ),
        ):
            existing = self._active_authority_for(
                projection, principal_ref=principal, required_operations=operations
            )
            if existing is not None:
                resolved_authority_ids[authority_id] = existing
                present.append(existing)
                continue
            committed.extend(
                self._commit_actor_authority(
                    authority_id=authority_id,
                    principal_ref=principal,
                    principal_kind=kind,
                    allowed_operations=operations,
                )
            )
            resolved_authority_ids[authority_id] = authority_id

        capability_ids = {item.grant_id for item in self._ledger.project().capability_grants}
        if ADULT_MEDIA_CAPABILITY_ID in capability_ids:
            present.append(ADULT_MEDIA_CAPABILITY_ID)
        else:
            committed.extend(
                self._commit_authorization(
                    domain="capability",
                    event_type="CapabilityGranted",
                    entity_id=ADULT_MEDIA_CAPABILITY_ID,
                    authority_id=resolved_authority_ids[_OPERATOR_AUTHORITY_ID],
                    principal_ref=self._operator_ref,
                    values={
                        "capability_kind": ADULT_MEDIA_CAPABILITY_KIND,
                        "actor_ref": MEDIA_CONTINUATION_ACTOR,
                        "target_scope_refs": ["provider:media"],
                        "constraint_refs": [],
                        "valid_from": None,
                        "expires_at": None,
                        "state": "active",
                    },
                )
            )

        consent_ids = {item.consent_id for item in self._ledger.project().consent_grants}
        if ADULT_MEDIA_CONSENT_ID in consent_ids:
            present.append(ADULT_MEDIA_CONSENT_ID)
        else:
            committed.extend(
                self._commit_authorization(
                    domain="consent",
                    event_type="ConsentGranted",
                    entity_id=ADULT_MEDIA_CONSENT_ID,
                    authority_id=resolved_authority_ids[_USER_AUTHORITY_ID],
                    principal_ref=self._subject_ref,
                    values={
                        "grantor_ref": self._subject_ref,
                        "grantee_ref": MEDIA_CONTINUATION_ACTOR,
                        "action_scope_refs": [ADULT_MEDIA_ACTION_SCOPE],
                        "data_scope_refs": ["data:attachment"],
                        "channel_scope_refs": [],
                        "valid_from": None,
                        "expires_at": None,
                        "revocable": True,
                        "status": "active",
                    },
                )
            )

        if committed:
            _LOG.warning(
                "world v2 adult media eligibility provisioned world=%s events=%d",
                self._ledger.world_id,
                len(committed),
            )
        return AdultMediaAuthorityProvisioningResult(
            committed_event_ids=tuple(committed), already_present=tuple(present)
        )

    def _commit_actor_authority(
        self,
        *,
        authority_id: str,
        principal_ref: str,
        principal_kind: str,
        allowed_operations: tuple[str, ...],
    ) -> list[str]:
        projection = self._ledger.project()
        logical_time = projection.logical_time
        transition_id = f"transition:{authority_id}"
        payload: dict[str, object] = {
            "world_id": self._ledger.world_id,
            "authority_id": authority_id,
            "transition_id": transition_id,
            "operation": "bootstrap",
            "expected_entity_revision": 0,
            "values_before": None,
            "values_after": {
                "principal_ref": principal_ref,
                "principal_kind": principal_kind,
                "credential_ref": f"credential:{principal_ref}",
                "allowed_operations": list(allowed_operations),
                "valid_from": logical_time.isoformat(),
                "expires_at": None,
                "status": "active",
            },
            "policy_version": "actor-authority-policy.1",
            "policy_digest": ACTOR_AUTHORITY_POLICY_DIGEST,
            "changed_at": logical_time.isoformat(),
            "compensates_transition_id": None,
            "root_proof": self._unsigned_proof(transition_id),
        }
        payload["root_proof"]["signed_mutation_hash"] = actor_authority_mutation_hash(payload)
        return self._commit_signed(
            event_id=f"event:adult-media-authority:{authority_id}",
            event_type="ActorAuthorityBootstrapped",
            payload=payload,
            mutation_hash=actor_authority_mutation_hash(payload),
            logical_time=logical_time,
        )

    def _commit_authorization(
        self,
        *,
        domain: str,
        event_type: str,
        entity_id: str,
        authority_id: str,
        principal_ref: str,
        values: dict[str, object],
    ) -> list[str]:
        projection = self._ledger.project()
        logical_time = projection.logical_time
        values = dict(values)
        values["valid_from"] = logical_time.isoformat()
        transition_id = f"transition:{entity_id}"
        payload: dict[str, object] = {
            "world_id": self._ledger.world_id,
            "entity_id": entity_id,
            "transition_id": transition_id,
            "operation": "grant",
            "expected_entity_revision": 0,
            "values_before": None,
            "values_after": values,
            "authority_id": authority_id,
            "expected_authority_revision": self._authority_revision(authority_id),
            "attested_principal_ref": principal_ref,
            "attestation_mode": "root_attested_external_principal_action.1",
            "attestation_environment": "enforcement",
            "principal_action_evidence": {
                "source_event_ref": f"evidence:{transition_id}",
                "payload_hash": _digest({"evidence": transition_id}),
                "authenticated_principal_ref": principal_ref,
                "action_ref": f"authorization:{domain}:grant",
                "scope_hash": authorization_scope_hash(domain, values),
                "intent_hash": "0" * 64,
                "challenge_ref": f"challenge:{transition_id}",
                "observed_at": logical_time.isoformat(),
                "expires_at": (logical_time + timedelta(minutes=5)).isoformat(),
                "authentication_policy_version": "external-principal-auth.enforcement.1",
                "authentication_policy_digest": ENFORCEMENT_EXTERNAL_PRINCIPAL_AUTH_POLICY_DIGEST,
            },
            "policy_version": {
                "capability": "capability-policy.1",
                "consent": "consent-policy.1",
            }[domain],
            "policy_digest": {
                "capability": CAPABILITY_POLICY_DIGEST,
                "consent": CONSENT_POLICY_DIGEST,
            }[domain],
            "changed_at": logical_time.isoformat(),
            "compensates_transition_id": None,
            "root_proof": self._unsigned_proof(transition_id),
        }
        payload["principal_action_evidence"]["intent_hash"] = authorization_intent_hash(
            domain, payload
        )
        payload["root_proof"]["signed_mutation_hash"] = authorization_mutation_hash(
            event_type, payload
        )
        return self._commit_signed(
            event_id=f"event:adult-media-authority:{entity_id}",
            event_type=event_type,
            payload=payload,
            mutation_hash=authorization_mutation_hash(event_type, payload),
            logical_time=logical_time,
        )

    def _unsigned_proof(self, transition_id: str) -> dict[str, object]:
        return {
            "keyset_version": ROOT_KEYSET_VERSION,
            "keyset_digest": ROOT_KEYSET_DIGEST,
            "root_key_id": self._root_key_id,
            "nonce": "nonce:"
            + _digest({"world": self._ledger.world_id, "t": transition_id})[:32],
            "signed_mutation_hash": "0" * 64,
            "signature_hex": "0" * 128,
        }

    def _commit_signed(
        self,
        *,
        event_id: str,
        event_type: str,
        payload: Mapping[str, object],
        mutation_hash: str,
        logical_time,
    ) -> list[str]:
        identity = domain_idempotency_key(
            event_type=event_type, world_id=self._ledger.world_id, payload=dict(payload)
        )
        if identity is None:
            raise ValueError(f"no identity contract for {event_type}")
        payload = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
        actor = "system:adult-media-authority-provisioning"
        source = "world-v2:adult-media-authority-provisioning"
        trace_id = f"trace:adult-media-authority:{event_id}"
        causation_id = f"provision:{event_id}"
        correlation_id = "correlation:adult-media-authority-provisioning"
        payload["root_proof"]["signature_hex"] = self._signing_key.sign(
            root_envelope_signature_message(
                schema_version="world-v2.1",
                world_id=self._ledger.world_id,
                event_type=event_type,
                event_id=event_id,
                actor=actor,
                source=source,
                logical_time=logical_time,
                created_at=logical_time,
                trace_id=trace_id,
                causation_id=causation_id,
                correlation_id=correlation_id,
                idempotency_key=identity,
                mutation_hash=mutation_hash,
            )
        ).signature.hex()
        projection = self._ledger.project()
        event = WorldEvent.from_payload(
            schema_version="world-v2.1",
            event_id=event_id,
            event_type=event_type,
            world_id=self._ledger.world_id,
            logical_time=logical_time,
            created_at=logical_time,
            actor=actor,
            source=source,
            trace_id=trace_id,
            causation_id=causation_id,
            correlation_id=correlation_id,
            idempotency_key=identity,
            payload=payload,
        )
        self._ledger.commit(
            (event,),
            expected_world_revision=projection.world_revision,
            expected_deliberation_revision=projection.deliberation_revision,
        )
        return [event.event_id]


__all__ = [
    "AdultMediaAuthorityProvisioner",
    "AdultMediaAuthorityProvisioningResult",
]
