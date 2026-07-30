using System;
using System.Collections;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.AI;
using UnityEngine.TestTools;
using NovelSim.Characters;
using NovelSim.Core;
using NovelSim.Interaction;
using NovelSim.Network;
using NovelSim.Visuals;
using NovelSim.World;
using Object = UnityEngine.Object;

namespace NovelSim.Tests
{
    public sealed class VerticalSliceSmokeTests
    {
        [UnityTest]
        public IEnumerator BootstrapCreatesPlayableServerAuthoritativeWorld()
        {
            var bootstrap = Object.FindFirstObjectByType<
                VerticalSliceBootstrap>();
            if (bootstrap == null)
            {
                bootstrap = new GameObject("NovelSim PlayMode Test")
                    .AddComponent<VerticalSliceBootstrap>();
            }

            yield return null;

            var player = GameObject.Find("Player");
            var npc = GameObject.Find("Ye Qingqing NPC");
            Assert.IsNotNull(player);
            Assert.IsNotNull(npc);
            Assert.IsNotNull(player.GetComponent<CharacterController>());
            Assert.IsNotNull(npc.GetComponent<InteractionTarget>());
            var playerPresentation =
                player.GetComponent<StylizedCharacterAnimator>();
            var npcPresentation =
                npc.GetComponent<StylizedCharacterAnimator>();
            Assert.IsNotNull(playerPresentation);
            Assert.IsNotNull(npcPresentation);
            Assert.IsTrue(playerPresentation.IsArticulated);
            Assert.IsTrue(npcPresentation.IsArticulated);
            Assert.GreaterOrEqual(
                player.GetComponentsInChildren<Renderer>().Length,
                24);
            Assert.GreaterOrEqual(
                npc.GetComponentsInChildren<Renderer>().Length,
                24);
            Assert.IsNotNull(GameObject.Find("Hero Front Skirt Left"));
            Assert.IsNotNull(GameObject.Find("High Ponytail Pivot"));
            Assert.IsNotNull(GameObject.Find("Ye Qingqing Visual"));
            Assert.IsNotNull(npc.GetComponent<NavMeshAgent>());
            Assert.IsNotNull(npc.GetComponent<NpcPatrolController>());
            var laneNavigation = Object.FindFirstObjectByType<
                RuntimeLaneNavMesh>();
            Assert.IsNotNull(laneNavigation);
            Assert.IsTrue(laneNavigation.IsReady);
            Assert.Greater(laneNavigation.SourceCount, 0);
            Assert.IsNotNull(GameObject.Find("Huarong Lane Art"));
            Assert.IsNotNull(GameObject.Find("Ground Mist"));
            Assert.IsNotNull(GameObject.Find("Veiled Moon"));
            Assert.GreaterOrEqual(
                Object.FindObjectsByType<AmbientSway>(
                    FindObjectsSortMode.None).Length,
                7);
            Assert.GreaterOrEqual(
                Object.FindObjectsByType<LanternFlicker>(
                    FindObjectsSortMode.None).Length,
                6);
            Assert.IsTrue(RenderSettings.fog);
            Assert.AreEqual(52f, Camera.main.fieldOfView, 0.1f);
            Assert.IsNotNull(Object.FindFirstObjectByType<
                WorldSessionManager>());
        }

        [UnityTest]
        public IEnumerator PresentationNavigateCommandUsesNavMeshAndArrives()
        {
            var player = GameObject.Find("Player");
            var npc = GameObject.Find("Ye Qingqing NPC");
            Assert.IsNotNull(player);
            Assert.IsNotNull(npc);
            player.transform.position = new Vector3(0f, 0.08f, -4f);
            var destination = new GameObject(
                "Presentation Navigation Destination");
            destination.transform.position =
                new Vector3(-1.35f, 0.08f, 9.4f);
            var host = new GameObject("Presentation Navigation Test");
            var registry = host.AddComponent<WorldEntityRegistry>();
            registry.RegisterEntity(
                "char_yeqingqing",
                npc.transform,
                "loc_huarong_lane");
            registry.RegisterLocation(
                "loc_navigation_test",
                destination.transform);
            var dispatcher = host.AddComponent<ToolEventDispatcher>();
            dispatcher.Configure(null, null, registry, null);

            Assert.IsTrue(dispatcher.Consume(
                new PresentationCommandDto
                {
                    sequence = 1001,
                    command_id = "event_000001:001:navigate",
                    command_type = "navigate",
                    actor_id = "char_yeqingqing",
                    location_id = "loc_navigation_test",
                }));
            var patrol = npc.GetComponent<NpcPatrolController>();
            Assert.IsNotNull(patrol);
            Assert.IsTrue(patrol.HasCommandDestination);

            var started = Time.realtimeSinceStartup;
            while (
                patrol.HasCommandDestination
                && Time.realtimeSinceStartup - started < 12f)
            {
                yield return null;
            }

            Assert.IsFalse(
                patrol.HasCommandDestination,
                "NavMesh command did not reach its destination.");
            Assert.Less(
                Vector3.Distance(
                    npc.transform.position,
                    destination.transform.position),
                0.55f);
            Object.Destroy(destination);
            Object.Destroy(host);
        }

        [UnityTest]
        public IEnumerator StoredSessionIsResumedInsteadOfCreatingANewOne()
        {
            WorldSessionManager.ClearSavedSession();
            PlayerPrefs.SetString(
                WorldSessionManager.LastSessionKey,
                "saved-session");
            PlayerPrefs.Save();
            var fake = new FakeApiClient();
            var host = new GameObject("Stored Session Test");
            var manager = host.AddComponent<WorldSessionManager>();
            manager.Configure(fake);

            manager.ResumeLastOrStart();
            yield return null;
            yield return null;

            Assert.AreEqual(1, fake.ResumeCalls);
            Assert.AreEqual(0, fake.StartCalls);
            Assert.AreEqual("saved-session", manager.SessionId);
            Assert.AreEqual(7, manager.State.version);
            Object.Destroy(host);
            WorldSessionManager.ClearSavedSession();
        }

        [UnityTest]
        public IEnumerator NearbyInteractionTargetReceivesVisualFocus()
        {
            var player = GameObject.Find("Player");
            var npc = GameObject.Find("Ye Qingqing NPC");
            Assert.IsNotNull(player);
            Assert.IsNotNull(npc);
            var interactor = player.GetComponent<PlayerInteractor>();
            var target = npc.GetComponent<InteractionTarget>();
            Assert.IsNotNull(interactor);
            Assert.IsNotNull(target);
            Assert.AreEqual("夜清清", target.DisplayName);
            StringAssert.Contains("夜清清", target.ServerAction);
            StringAssert.DoesNotContain("守卫", target.ServerAction);

            player.transform.position =
                npc.transform.position + npc.transform.forward * 1.2f;
            Physics.SyncTransforms();
            yield return null;

            Assert.AreSame(target, interactor.CurrentTarget);
            Assert.IsTrue(target.IsFocused);
        }

        [UnityTest]
        public IEnumerator InteractionPathSubmitsTargetActionAndAdvancesState()
        {
            WorldSessionManager.ClearSavedSession();
            var fake = new FakeApiClient();
            var host = new GameObject("Interaction Session Test");
            var manager = host.AddComponent<WorldSessionManager>();
            manager.Configure(fake);
            manager.StartNewSession();
            yield return null;
            yield return null;

            var player = new GameObject("Interaction Test Player");
            player.transform.position = Vector3.zero;
            var interactor = player.AddComponent<PlayerInteractor>();
            interactor.Configure(manager, null);
            var npc = new GameObject("Interaction Test NPC");
            npc.transform.position = new Vector3(0f, 0f, 1f);
            npc.AddComponent<SphereCollider>();
            npc.AddComponent<InteractionTarget>().Configure(
                "测试守卫",
                "询问真实世界状态");
            Physics.SyncTransforms();

            Assert.IsTrue(interactor.TryInteract());
            yield return null;
            yield return null;

            Assert.AreEqual(1, fake.SubmitCalls);
            Assert.AreEqual("询问真实世界状态", fake.LastAction);
            Assert.AreEqual(1, manager.State.version);
            Assert.IsNotNull(manager.LastTurn);
            Object.Destroy(player);
            Object.Destroy(npc);
            Object.Destroy(host);
            WorldSessionManager.ClearSavedSession();
        }

        [UnityTest]
        public IEnumerator SecretLetterRouteAdoptsPersistentAuthoritativeSession()
        {
            WorldSessionManager.ClearSavedSession();
            var fake = new FakeApiClient();
            var host = new GameObject("Secret Letter Route Test");
            var manager = host.AddComponent<WorldSessionManager>();
            manager.Configure(fake);

            manager.RunSecretLetterRoute("expose_truth");
            yield return null;
            yield return null;

            Assert.AreEqual("secret-letter-session", manager.SessionId);
            Assert.AreEqual(5, manager.State.version);
            Assert.AreEqual(
                "truth_exposed",
                manager.LastSceneRun.ending);
            Assert.AreEqual(
                manager.SessionId,
                PlayerPrefs.GetString(
                    WorldSessionManager.LastSessionKey));
            Object.Destroy(host);
            WorldSessionManager.ClearSavedSession();
        }

        [UnityTest]
        public IEnumerator SnapshotCreatesPreviouslyUnknownRuntimeNpc()
        {
            var host = new GameObject("Dynamic NPC Reconcile Test");
            var registry = host.AddComponent<WorldEntityRegistry>();

            registry.Reconcile(new PresentationSnapshotDto
            {
                characters = new[]
                {
                    new CharacterPresentationStateDto
                    {
                        character_id = "char_steward",
                        display_name = "管家",
                        location_id = "loc_gatehouse",
                        is_alive = true,
                    },
                },
                items = Array.Empty<ItemPresentationStateDto>(),
                alliances = Array.Empty<AlliancePresentationStateDto>(),
            });
            yield return null;

            Assert.IsTrue(
                registry.TryGetEntity(
                    "char_steward",
                    out var steward));
            Assert.IsTrue(steward.gameObject.activeSelf);
            Assert.IsNotNull(
                steward.GetComponent<NpcPatrolController>());
            Object.Destroy(host);
            yield return null;
        }

        private sealed class FakeApiClient : INovelSimApiClient
        {
            public int StartCalls { get; private set; }
            public int ResumeCalls { get; private set; }
            public int SubmitCalls { get; private set; }
            public string LastAction { get; private set; }

            public IEnumerator StartSession(
                string packageId,
                Action<SessionResponse> onSuccess,
                Action<string> onFailure)
            {
                StartCalls++;
                yield return null;
                onSuccess(new SessionResponse
                {
                    status = "ok",
                    session_id = "new-session",
                    state = State(0),
                    world_meta = new WorldMetaDto
                    {
                        scenario = "测试世界",
                    },
                    resumed = false,
                });
            }

            public IEnumerator ResumeSession(
                string sessionId,
                Action<SessionResponse> onSuccess,
                Action<string> onFailure)
            {
                ResumeCalls++;
                yield return null;
                onSuccess(new SessionResponse
                {
                    status = "ok",
                    session_id = sessionId,
                    state = State(7),
                    save = new SaveDto
                    {
                        session_id = sessionId,
                        name = "自动存档",
                    },
                    resumed = true,
                });
            }

            public IEnumerator SubmitTurn(
                string sessionId,
                string text,
                Action<TurnResponse> onSuccess,
                Action<string> onFailure)
            {
                SubmitCalls++;
                LastAction = text;
                yield return null;
                onSuccess(new TurnResponse
                {
                    status = "ok",
                    state = State(1),
                    narrative = new NarrativeDto
                    {
                        narration = "守卫给出了真实回应。",
                    },
                });
            }

            public IEnumerator RunSecretLetterScene(
                string route,
                Action<SecretLetterRunResponse> onSuccess,
                Action<string> onFailure)
            {
                yield return null;
                onSuccess(new SecretLetterRunResponse
                {
                    status = "completed",
                    session_id = "secret-letter-session",
                    default_actor = "char_player",
                    route = route,
                    ending = "truth_exposed",
                    objective_satisfied = true,
                    state = State(5),
                    memory_record_count = 7,
                    world_meta = new WorldMetaDto
                    {
                        scenario = "午夜前的密信",
                    },
                });
            }

            public IEnumerator FetchPresentationSnapshot(
                string sessionId,
                Action<PresentationSnapshotResponse> onSuccess,
                Action<string> onFailure)
            {
                yield return null;
                onSuccess(new PresentationSnapshotResponse
                {
                    status = "ok",
                    session_id = sessionId,
                    snapshot = new PresentationSnapshotDto
                    {
                        timeline_id = "test-timeline",
                        state_version = 1,
                        current_scene_id = "lane",
                        last_sequence = 1999,
                    },
                });
            }

            public IEnumerator FetchPresentationEvents(
                string sessionId,
                long afterSequence,
                Action<PresentationEventsResponse> onSuccess,
                Action<string> onFailure)
            {
                yield return null;
                onSuccess(new PresentationEventsResponse
                {
                    status = "ok",
                    session_id = sessionId,
                    after_sequence = afterSequence,
                    next_sequence = afterSequence,
                    commands = Array.Empty<PresentationCommandDto>(),
                });
            }

            private static WorldStateDto State(int version)
            {
                return new WorldStateDto
                {
                    timeline_id = "test-timeline",
                    version = version,
                    world_time = "雨夜",
                    current_scene_id = "lane",
                };
            }
        }
    }
}
