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
            var npc = GameObject.Find("Lane Guard NPC");
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
                30);
            Assert.IsNotNull(GameObject.Find("Hero Front Skirt Left"));
            Assert.IsNotNull(GameObject.Find("High Ponytail Pivot"));
            Assert.IsNotNull(GameObject.Find("Armor Rivet 0 0"));
            Assert.IsNotNull(GameObject.Find("Guard Helmet Flap Left"));
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
            var npc = GameObject.Find("Lane Guard NPC");
            Assert.IsNotNull(player);
            Assert.IsNotNull(npc);
            var interactor = player.GetComponent<PlayerInteractor>();
            var target = npc.GetComponent<InteractionTarget>();
            Assert.IsNotNull(interactor);
            Assert.IsNotNull(target);

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
