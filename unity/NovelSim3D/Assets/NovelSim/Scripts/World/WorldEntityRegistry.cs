using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.AI;
using NovelSim.Characters;
using NovelSim.Network;
using NovelSim.Visuals;

namespace NovelSim.World
{
    public sealed class WorldEntityBinding : MonoBehaviour
    {
        [SerializeField]
        private string entityId;

        [SerializeField]
        private string currentLocationId;

        public string EntityId => entityId;
        public string CurrentLocationId => currentLocationId;

        public void Configure(string value, string locationId = "")
        {
            entityId = value ?? string.Empty;
            currentLocationId = locationId ?? string.Empty;
        }

        public void SetLocation(string locationId)
        {
            currentLocationId = locationId ?? string.Empty;
        }
    }

    /// <summary>
    /// Maps authoritative entity/location ids to runtime objects.
    /// Snapshot reconciliation is immediate; new commands may animate from it.
    /// </summary>
    public sealed class WorldEntityRegistry : MonoBehaviour
    {
        private readonly Dictionary<string, Transform> entities =
            new Dictionary<string, Transform>(StringComparer.Ordinal);
        private readonly Dictionary<string, Transform> locations =
            new Dictionary<string, Transform>(StringComparer.Ordinal);
        private readonly Dictionary<string, GameObject> runtimeItems =
            new Dictionary<string, GameObject>(StringComparer.Ordinal);
        private readonly Dictionary<string, GameObject> runtimeCharacters =
            new Dictionary<string, GameObject>(StringComparer.Ordinal);

        public void RegisterEntity(
            string entityId,
            Transform target,
            string currentLocationId = "")
        {
            if (string.IsNullOrWhiteSpace(entityId) || target == null)
            {
                return;
            }
            entities[entityId] = target;
            var binding = target.GetComponent<WorldEntityBinding>();
            if (binding == null)
            {
                binding = target.gameObject.AddComponent<WorldEntityBinding>();
            }
            binding.Configure(entityId, currentLocationId);
        }

        public void RegisterLocation(string locationId, Transform anchor)
        {
            if (string.IsNullOrWhiteSpace(locationId) || anchor == null)
            {
                return;
            }
            locations[locationId] = anchor;
        }

        public bool TryGetEntity(string entityId, out Transform target)
        {
            return entities.TryGetValue(entityId ?? string.Empty, out target);
        }

        public bool TryGetLocation(string locationId, out Transform anchor)
        {
            return locations.TryGetValue(locationId ?? string.Empty, out anchor);
        }

        public bool Navigate(string entityId, string locationId)
        {
            if (
                !TryGetEntity(entityId, out var entity)
                || !TryGetLocation(locationId, out var anchor))
            {
                return false;
            }
            var patrol = entity.GetComponent<NpcPatrolController>();
            if (patrol != null)
            {
                var accepted = patrol.NavigateTo(anchor.position);
                if (accepted)
                {
                    entity.GetComponent<WorldEntityBinding>()
                        ?.SetLocation(locationId);
                }
                return accepted;
            }
            ReconcileTransform(entity, anchor.position);
            entity.GetComponent<WorldEntityBinding>()
                ?.SetLocation(locationId);
            return true;
        }

        public bool Face(string actorId, string targetId)
        {
            if (
                !TryGetEntity(actorId, out var actor)
                || !TryGetEntity(targetId, out var target))
            {
                return false;
            }
            var patrol = actor.GetComponent<NpcPatrolController>();
            if (patrol != null)
            {
                patrol.Face(target);
                return true;
            }
            var direction = target.position - actor.position;
            direction.y = 0f;
            if (direction.sqrMagnitude > 0.001f)
            {
                actor.rotation = Quaternion.LookRotation(
                    direction.normalized,
                    Vector3.up);
            }
            return true;
        }

        public void Reconcile(PresentationSnapshotDto snapshot)
        {
            if (snapshot == null)
            {
                return;
            }
            foreach (var runtimeCharacter in runtimeCharacters.Values)
            {
                if (runtimeCharacter != null)
                {
                    runtimeCharacter.SetActive(false);
                }
            }
            foreach (var character in snapshot.characters
                ?? Array.Empty<CharacterPresentationStateDto>())
            {
                if (!TryGetEntity(character.character_id, out var entity))
                {
                    entity = CreateRuntimeCharacter(character);
                }
                entity.gameObject.SetActive(character.is_alive);
                var binding = entity.GetComponent<WorldEntityBinding>();
                if (
                    character.is_alive
                    && binding != null
                    && !string.Equals(
                        binding.CurrentLocationId,
                        character.location_id,
                        StringComparison.Ordinal)
                    && TryGetLocation(character.location_id, out var anchor))
                {
                    ReconcileTransform(entity, anchor.position);
                    binding.SetLocation(character.location_id);
                }
            }
            foreach (var item in snapshot.items
                ?? Array.Empty<ItemPresentationStateDto>())
            {
                ReconcileItem(item);
            }
        }

        private Transform CreateRuntimeCharacter(
            CharacterPresentationStateDto state)
        {
            var character = new GameObject(
                string.IsNullOrWhiteSpace(state.display_name)
                    ? state.character_id
                    : state.display_name);
            character.transform.SetParent(transform);
            var offset = runtimeCharacters.Count;
            character.transform.position = new Vector3(
                -2.8f + offset * 1.8f,
                0.08f,
                7.2f + (offset % 2) * 1.6f);
            var collider = character.AddComponent<CapsuleCollider>();
            collider.center = new Vector3(0f, 1.36f, 0f);
            collider.height = 2.72f;
            collider.radius = 0.5f;
            StylizedCharacterFactory.BuildGuard(character.transform);
            var nav = character.AddComponent<NavMeshAgent>();
            nav.baseOffset = 0.06f;
            character.AddComponent<NpcPatrolController>().Configure(null);
            runtimeCharacters[state.character_id] = character;
            RegisterEntity(
                state.character_id,
                character.transform,
                state.location_id);
            return character.transform;
        }

        public void SetItemUnavailable(string itemId)
        {
            if (
                runtimeItems.TryGetValue(itemId ?? string.Empty, out var item)
                && item != null)
            {
                item.SetActive(false);
            }
        }

        private void ReconcileItem(ItemPresentationStateDto state)
        {
            if (state == null || string.IsNullOrWhiteSpace(state.item_id))
            {
                return;
            }
            var visible = (
                !state.destroyed
                && state.quantity > 0
                && state.accessible
                && string.IsNullOrWhiteSpace(state.owner_id)
                && !string.IsNullOrWhiteSpace(state.location_id)
            );
            if (!runtimeItems.TryGetValue(state.item_id, out var item))
            {
                if (!visible)
                {
                    return;
                }
                item = CreateRuntimeItem(state);
                runtimeItems[state.item_id] = item;
            }
            item.SetActive(visible);
            if (
                visible
                && TryGetLocation(state.location_id, out var anchor))
            {
                item.transform.position =
                    anchor.position + new Vector3(0.65f, 0.35f, 0.2f);
            }
        }

        private GameObject CreateRuntimeItem(ItemPresentationStateDto state)
        {
            var item = GameObject.CreatePrimitive(PrimitiveType.Cube);
            item.name = string.IsNullOrWhiteSpace(state.display_name)
                ? state.item_id
                : state.display_name;
            item.transform.SetParent(transform);
            item.transform.localScale = new Vector3(0.42f, 0.08f, 0.3f);
            RegisterEntity(state.item_id, item.transform);
            return item;
        }

        private static void ReconcileTransform(
            Transform target,
            Vector3 position)
        {
            var nav = target.GetComponent<NavMeshAgent>();
            if (
                nav != null
                && nav.enabled
                && NavMesh.SamplePosition(
                    position,
                    out var hit,
                    2.5f,
                    NavMesh.AllAreas))
            {
                nav.Warp(hit.position);
                return;
            }
            var controller = target.GetComponent<CharacterController>();
            if (controller != null)
            {
                controller.enabled = false;
                target.position = position;
                controller.enabled = true;
                return;
            }
            target.position = position;
        }
    }
}
