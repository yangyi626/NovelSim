using NUnit.Framework;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;
using NovelSim.Characters;
using NovelSim.Network;
using NovelSim.World;

namespace NovelSim.Tests
{
    public sealed class ApiContractTests
    {
        [Test]
        public void ContractMajorVersionIsFrozen()
        {
            Assert.AreEqual("1.0.0", ApiContractV1.Version);
            Assert.AreEqual(1, ApiContractV1.MajorVersion);
            Assert.AreEqual("/api/start", ApiContractV1.StartSession);
            Assert.AreEqual(
                "/api/scenes/secret-letter/runs",
                ApiContractV1.SecretLetterRun);
            Assert.AreEqual("/api/turn", ApiContractV1.SubmitTurn);
            Assert.AreEqual(
                "/api/presentation-events",
                ApiContractV1.PresentationEvents);
        }

        [Test]
        public void TurnResponseParsesAuthoritativeStateAndNarrative()
        {
            const string json =
                "{\"status\":\"committed\",\"state\":{\"timeline_id\":\"t\","
                + "\"version\":2,\"world_time\":\"午后\","
                + "\"current_scene_id\":\"lane\"},\"narrative\":{"
                + "\"narration\":\"她走近守卫。\",\"dialogues\":[]}}";

            var response = JsonUtility.FromJson<TurnResponse>(json);

            Assert.AreEqual(2, response.state.version);
            Assert.AreEqual("lane", response.state.current_scene_id);
            Assert.AreEqual("她走近守卫。", response.narrative.narration);
        }

        [Test]
        public void ProjectUsesUniversalRenderPipeline()
        {
            Assert.IsInstanceOf<UniversalRenderPipelineAsset>(
                GraphicsSettings.defaultRenderPipeline);
        }

        [Test]
        public void PresentationCommandParsesStableCursorFields()
        {
            const string json =
                "{\"status\":\"ok\",\"next_sequence\":2001,"
                + "\"commands\":[{\"sequence\":2001,"
                + "\"command_id\":\"event_000002:001:navigate\","
                + "\"event_id\":\"event_000002\",\"world_version\":2,"
                + "\"command_type\":\"navigate\","
                + "\"actor_id\":\"char_guard\","
                + "\"location_id\":\"loc_gate\"}]}";

            var response = JsonUtility.FromJson<
                PresentationEventsResponse>(json);

            Assert.AreEqual(2001L, response.next_sequence);
            Assert.AreEqual(1, response.commands.Length);
            Assert.AreEqual("navigate", response.commands[0].command_type);
            Assert.AreEqual("loc_gate", response.commands[0].location_id);
        }

        [Test]
        public void DispatcherConsumesDuplicateSequenceOnlyOnce()
        {
            var host = new GameObject("Dispatcher EditMode Test");
            var registry = host.AddComponent<WorldEntityRegistry>();
            var dispatcher = host.AddComponent<ToolEventDispatcher>();
            dispatcher.ApplySnapshot(new PresentationSnapshotDto
            {
                last_sequence = 999,
                characters = new CharacterPresentationStateDto[0],
                items = new ItemPresentationStateDto[0],
                alliances = new AlliancePresentationStateDto[0],
            });
            var command = new PresentationCommandDto
            {
                sequence = 1001,
                command_id = "event_000001:001:system_hint",
                command_type = "system_hint",
                text = "只显示一次",
            };

            Assert.IsTrue(dispatcher.Consume(command));
            Assert.IsFalse(dispatcher.Consume(command));
            Assert.AreEqual(1001L, dispatcher.LastAcknowledgedSequence);
            Assert.AreEqual(1, dispatcher.DispatchedCommandCount);
            Object.DestroyImmediate(host);
        }

        [Test]
        public void DispatcherMapsNavigateToRegisteredLocation()
        {
            var host = new GameObject("Navigation Dispatch Test");
            var entity = new GameObject("Actor");
            var anchor = new GameObject("Destination");
            anchor.transform.position = new Vector3(3f, 0f, 4f);
            var registry = host.AddComponent<WorldEntityRegistry>();
            registry.RegisterEntity("char_actor", entity.transform, "loc_a");
            registry.RegisterLocation("loc_b", anchor.transform);
            var dispatcher = host.AddComponent<ToolEventDispatcher>();
            dispatcher.Configure(null, null, registry, null);

            Assert.IsTrue(dispatcher.Consume(new PresentationCommandDto
            {
                sequence = 1001,
                command_id = "event_000001:001:navigate",
                command_type = "navigate",
                actor_id = "char_actor",
                location_id = "loc_b",
            }));
            Assert.AreEqual(anchor.transform.position, entity.transform.position);

            Object.DestroyImmediate(anchor);
            Object.DestroyImmediate(entity);
            Object.DestroyImmediate(host);
        }

    }
}
