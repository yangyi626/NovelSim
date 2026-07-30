using System;
using System.Collections;
using System.IO;
using UnityEngine;
using UnityEngine.AI;
using NovelSim.Characters;
using NovelSim.Interaction;
using NovelSim.Network;
using NovelSim.UI;
using NovelSim.Visuals;
using NovelSim.World;

namespace NovelSim.Core
{
    /// <summary>
    /// Builds the playable rain-night Huarong Lane vertical slice.
    /// </summary>
    public sealed class VerticalSliceBootstrap : MonoBehaviour
    {
        private const string DefaultApiUrl = "http://127.0.0.1:8000";

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void EnsureBootstrap()
        {
            if (FindFirstObjectByType<VerticalSliceBootstrap>() != null)
            {
                return;
            }
            new GameObject("NovelSim Vertical Slice")
                .AddComponent<VerticalSliceBootstrap>();
        }

        private void Awake()
        {
            var systems = new GameObject("NovelSim Systems");
            systems.transform.SetParent(transform);
            var api = systems.AddComponent<NovelSimApiClient>();
            var commandLineApiUrl = ArgumentValue(
                Environment.GetCommandLineArgs(),
                "-novelsim-api-url");
            api.Configure(
                string.IsNullOrWhiteSpace(commandLineApiUrl)
                    ? PlayerPrefs.GetString(
                        "NovelSim.ApiBaseUrl",
                        DefaultApiUrl)
                    : commandLineApiUrl);
            var session = systems.AddComponent<WorldSessionManager>();
            session.Configure(api);
            var hud = systems.AddComponent<NovelSimHud>();
            hud.Configure(session, api);

            var cameraTransform = EnsureCamera();
            CreateLighting();
            var environment =
                HuarongLaneVisualDirector.BuildEnvironment(transform);
            var navigation = systems.AddComponent<RuntimeLaneNavMesh>();
            navigation.Build(environment);
            var interactor = CreatePlayer(cameraTransform, session, hud);
            var npc = CreateNpc(interactor.transform);
            var entityRegistry =
                systems.AddComponent<WorldEntityRegistry>();
            entityRegistry.RegisterEntity(
                "char_yeqingge",
                interactor.transform,
                "loc_huarong_lane");
            entityRegistry.RegisterEntity(
                "char_player",
                interactor.transform,
                "loc_gatehouse");
            entityRegistry.RegisterEntity(
                "char_yeqingqing",
                npc.transform,
                "loc_huarong_lane");
            entityRegistry.RegisterEntity(
                "char_guard",
                npc.transform,
                "loc_gatehouse");
            entityRegistry.RegisterLocation(
                "loc_huarong_lane",
                CreateLocationAnchor(
                    "Huarong Lane Anchor",
                    new Vector3(0f, 0.08f, 5f)));
            entityRegistry.RegisterLocation(
                "loc_yefu",
                CreateLocationAnchor(
                    "Ye Residence Anchor",
                    new Vector3(0f, 0.08f, 13.5f)));
            entityRegistry.RegisterLocation(
                "loc_gatehouse",
                CreateLocationAnchor(
                    "Secret Letter Gatehouse Anchor",
                    new Vector3(0f, 0.08f, 5f)));
            entityRegistry.RegisterLocation(
                "loc_courtyard",
                CreateLocationAnchor(
                    "Secret Letter Courtyard Anchor",
                    new Vector3(0f, 0.08f, 13.5f)));
            var presentationDispatcher =
                systems.AddComponent<ToolEventDispatcher>();
            presentationDispatcher.Configure(
                session,
                api,
                entityRegistry,
                hud);
            gameObject.AddComponent<StandaloneInteractionSmokeRunner>()
                .Configure(
                    session,
                    interactor,
                    npc,
                    presentationDispatcher);
            gameObject.AddComponent<StandaloneVisualCaptureRunner>()
                .Configure(interactor.transform, npc.transform);
            gameObject.AddComponent<StandaloneShowcaseRunner>()
                .Configure(
                    session,
                    interactor,
                    npc,
                    presentationDispatcher,
                    hud);
            StartCoroutine(StartSessionNextFrame(session));
        }

        private static IEnumerator StartSessionNextFrame(
            WorldSessionManager session)
        {
            yield return null;
            session.ResumeLastOrStart();
        }

        private static Transform EnsureCamera()
        {
            var existing = Camera.main;
            if (existing != null)
            {
                ConfigureCamera(existing);
                return existing.transform;
            }
            var cameraObject = new GameObject("Main Camera");
            cameraObject.tag = "MainCamera";
            var sceneCamera = cameraObject.AddComponent<Camera>();
            ConfigureCamera(sceneCamera);
            cameraObject.AddComponent<AudioListener>();
            cameraObject.transform.position = new Vector3(0f, 3.2f, -11f);
            return cameraObject.transform;
        }

        private static void ConfigureCamera(Camera sceneCamera)
        {
            sceneCamera.fieldOfView = 52f;
            sceneCamera.nearClipPlane = 0.12f;
            sceneCamera.farClipPlane = 90f;
            sceneCamera.allowHDR = true;
            sceneCamera.clearFlags = CameraClearFlags.SolidColor;
            sceneCamera.backgroundColor =
                new Color(0.018f, 0.038f, 0.062f);
        }

        private void CreateLighting()
        {
            var lightObject = new GameObject("Rain Moon");
            var light = lightObject.AddComponent<Light>();
            light.type = LightType.Directional;
            light.color = new Color(0.52f, 0.68f, 0.9f);
            light.intensity = 1.16f;
            light.shadows = LightShadows.Soft;
            light.shadowStrength = 0.72f;
            lightObject.transform.SetParent(transform);
            lightObject.transform.rotation = Quaternion.Euler(52f, -28f, 0f);

            var fillObject = new GameObject("Lantern Character Fill");
            fillObject.transform.SetParent(transform);
            fillObject.transform.position = new Vector3(-2.4f, 3.7f, 2.5f);
            var fill = fillObject.AddComponent<Light>();
            fill.type = LightType.Point;
            fill.color = new Color(1f, 0.35f, 0.12f);
            fill.intensity = 5.2f;
            fill.range = 11.5f;
            fill.shadows = LightShadows.None;

            var rimObject = new GameObject("Rain Blue Rim");
            rimObject.transform.SetParent(transform);
            rimObject.transform.position = new Vector3(2f, 5.5f, 11f);
            rimObject.transform.rotation = Quaternion.Euler(42f, 194f, 0f);
            var rim = rimObject.AddComponent<Light>();
            rim.type = LightType.Spot;
            rim.color = new Color(0.2f, 0.5f, 0.82f);
            rim.intensity = 5f;
            rim.range = 17f;
            rim.spotAngle = 58f;
            rim.innerSpotAngle = 32f;
            rim.shadows = LightShadows.Soft;
        }

        private Transform CreateLocationAnchor(
            string anchorName,
            Vector3 position)
        {
            var anchor = new GameObject(anchorName);
            anchor.transform.SetParent(transform);
            anchor.transform.position = position;
            return anchor.transform;
        }

        private InteractionTarget CreateNpc(Transform player)
        {
            var npc = new GameObject("Ye Qingqing NPC");
            npc.transform.SetParent(transform);
            npc.transform.position = new Vector3(0.85f, 0.08f, 6f);
            npc.transform.rotation = Quaternion.Euler(0f, 198f, 0f);
            var collider = npc.AddComponent<CapsuleCollider>();
            collider.center = new Vector3(0f, 1.36f, 0f);
            collider.height = 2.72f;
            collider.radius = 0.52f;
            var target = npc.AddComponent<InteractionTarget>();
            target.Configure(
                "夜清清",
                "我冷冷地命令夜清清把她的外衫脱下来给我");
            HuarongLaneVisualDirector.BuildQingqingVisual(npc.transform);
            var agent = npc.AddComponent<NavMeshAgent>();
            agent.baseOffset = 0.06f;
            npc.AddComponent<NpcPatrolController>().Configure(
                player,
                new Vector3(0.85f, 0.08f, 6f),
                new Vector3(-1.35f, 0.08f, 9.4f),
                new Vector3(1.8f, 0.08f, 12.2f),
                new Vector3(-0.6f, 0.08f, 8f));
            return target;
        }

        private PlayerInteractor CreatePlayer(
            Transform cameraTransform,
            WorldSessionManager session,
            NovelSimHud hud)
        {
            var player = new GameObject("Player");
            player.transform.SetParent(transform);
            player.transform.position = new Vector3(0f, 0.08f, -5.5f);
            var controller = player.AddComponent<CharacterController>();
            controller.height = 2.72f;
            controller.radius = 0.5f;
            controller.center = new Vector3(0f, 1.36f, 0f);
            controller.skinWidth = 0.06f;
            player.AddComponent<ThirdPersonMotor>().Configure(cameraTransform);
            var interactor = player.AddComponent<PlayerInteractor>();
            interactor.Configure(session, hud);
            HuarongLaneVisualDirector.BuildPlayerVisual(player.transform);
            return interactor;
        }

        private static string ArgumentValue(string[] args, string name)
        {
            for (var index = 0; index + 1 < args.Length; index++)
            {
                if (string.Equals(
                    args[index],
                    name,
                    StringComparison.OrdinalIgnoreCase))
                {
                    return args[index + 1];
                }
            }
            return string.Empty;
        }
    }

    /// <summary>
    /// Windows 独立包的真实 HTTP 交互与存档恢复验收入口。
    /// 只在显式命令行参数存在时启用，不影响正常玩家流程。
    /// </summary>
    internal sealed class StandaloneInteractionSmokeRunner : MonoBehaviour
    {
        private const float SessionTimeout = 120f;
        private const float TurnTimeout = 360f;
        private const float PresentationTimeout = 60f;

        private WorldSessionManager session;
        private PlayerInteractor interactor;
        private InteractionTarget target;
        private ToolEventDispatcher presentationDispatcher;
        private bool enabledRunner;
        private bool resumeOnly;
        private string secretLetterRoute;
        private string reportPath;

        public void Configure(
            WorldSessionManager manager,
            PlayerInteractor playerInteractor,
            InteractionTarget interactionTarget,
            ToolEventDispatcher dispatcher)
        {
            session = manager;
            interactor = playerInteractor;
            target = interactionTarget;
            presentationDispatcher = dispatcher;
            var args = Environment.GetCommandLineArgs();
            enabledRunner = HasArgument(args, "-novelsim-smoke-interact")
                || HasArgument(args, "-novelsim-smoke-resume")
                || HasArgument(args, "-novelsim-smoke-secret-letter");
            if (!enabledRunner)
            {
                return;
            }
            resumeOnly = HasArgument(args, "-novelsim-smoke-resume");
            secretLetterRoute = ArgumentValue(
                args,
                "-novelsim-smoke-secret-letter");
            reportPath = ArgumentValue(
                args,
                "-novelsim-smoke-report");
            if (HasArgument(args, "-novelsim-new-session"))
            {
                WorldSessionManager.ClearSavedSession();
            }
            StartCoroutine(Run());
        }

        private IEnumerator Run()
        {
            var started = Time.realtimeSinceStartup;
            while (!session.HasSession)
            {
                if (!string.IsNullOrWhiteSpace(session.LastError))
                {
                    Finish("failed", session.LastError, 2);
                    yield break;
                }
                if (Time.realtimeSinceStartup - started > SessionTimeout)
                {
                    Finish("failed", "等待世界线超时", 3);
                    yield break;
                }
                yield return null;
            }

            if (resumeOnly)
            {
                started = Time.realtimeSinceStartup;
                var expectedSequence =
                    (session.State?.version ?? 0) * 1000L + 1L;
                while (
                    presentationDispatcher != null
                    && (
                        presentationDispatcher.IsSyncing
                        || presentationDispatcher.LastAcknowledgedSequence
                            < expectedSequence))
                {
                    if (
                        Time.realtimeSinceStartup - started
                        > PresentationTimeout)
                    {
                        Finish(
                            "failed",
                            "等待表现快照恢复超时",
                            7);
                        yield break;
                    }
                    yield return null;
                }
                Finish("resume_ok", "存档恢复成功", 0);
                yield break;
            }

            if (!string.IsNullOrWhiteSpace(secretLetterRoute))
            {
                yield return RunSecretLetterRoute();
                yield break;
            }

            var initialVersion = session.State?.version ?? 0;
            interactor.transform.position = target.transform.position
                + target.transform.forward * 1.2f;
            Physics.SyncTransforms();
            yield return null;
            if (!interactor.TryInteract())
            {
                Finish("failed", "E 交互目标未进入有效范围", 4);
                yield break;
            }

            started = Time.realtimeSinceStartup;
            while (session.LastTurn == null)
            {
                if (!string.IsNullOrWhiteSpace(session.LastError))
                {
                    Finish("failed", session.LastError, 5);
                    yield break;
                }
                if (Time.realtimeSinceStartup - started > TurnTimeout)
                {
                    Finish("failed", "等待真实回合超时", 6);
                    yield break;
                }
                yield return null;
            }

            if ((session.State?.version ?? 0) <= initialVersion)
            {
                var failure = string.IsNullOrWhiteSpace(
                    session.LastTurn.rejection_message)
                    ? $"回合状态为 {session.LastTurn.status}"
                    : session.LastTurn.rejection_message;
                Finish("failed", failure, 8);
                yield break;
            }

            started = Time.realtimeSinceStartup;
            var minimumSequence =
                (session.State?.version ?? 0) * 1000L + 1L;
            while (
                presentationDispatcher != null
                && (
                    presentationDispatcher.IsSyncing
                    || presentationDispatcher.DispatchedCommandCount < 1
                    || presentationDispatcher.LastAcknowledgedSequence
                        < minimumSequence))
            {
                if (
                    Time.realtimeSinceStartup - started
                    > PresentationTimeout)
                {
                    Finish(
                        "failed",
                        "世界已推进，但表现事件未被客户端消费",
                        9);
                    yield break;
                }
                yield return null;
            }
            Finish("interaction_ok", "真实 E 交互完成", 0);
        }

        private IEnumerator RunSecretLetterRoute()
        {
            var expectedEnding = ExpectedSecretLetterEnding(
                secretLetterRoute);
            if (string.IsNullOrWhiteSpace(expectedEnding))
            {
                Finish(
                    "failed",
                    $"未知密信路线：{secretLetterRoute}",
                    10);
                yield break;
            }

            session.RunSecretLetterRoute(secretLetterRoute);
            var started = Time.realtimeSinceStartup;
            while (session.LastSceneRun == null)
            {
                if (!string.IsNullOrWhiteSpace(session.LastError))
                {
                    Finish("failed", session.LastError, 11);
                    yield break;
                }
                if (Time.realtimeSinceStartup - started > TurnTimeout)
                {
                    Finish("failed", "等待密信路线完成超时", 12);
                    yield break;
                }
                yield return null;
            }

            var result = session.LastSceneRun;
            if (
                result.status != "completed"
                || result.world_package_id != "secret_letter_v1"
                || result.route != secretLetterRoute
                || result.ending != expectedEnding
                || result.session_id != session.SessionId
                || result.state == null
                || result.state.timeline_id != session.State?.timeline_id
                || result.state.version <= 0
                || result.memory_record_count < result.state.version)
            {
                Finish(
                    "failed",
                    "密信路线响应与权威状态不一致",
                    13);
                yield break;
            }

            started = Time.realtimeSinceStartup;
            while (
                presentationDispatcher != null
                && (
                    presentationDispatcher.IsSyncing
                    || presentationDispatcher.LastAcknowledgedSequence
                        < result.presentation_cursor))
            {
                if (
                    Time.realtimeSinceStartup - started
                    > PresentationTimeout)
                {
                    Finish(
                        "failed",
                        "等待密信路线表现快照恢复超时",
                        14);
                    yield break;
                }
                yield return null;
            }

            Finish(
                "secret_letter_ok",
                $"密信路线完成：{result.route} → {result.ending}",
                0);
        }

        private static string ExpectedSecretLetterEnding(string route)
        {
            switch (route)
            {
                case "destroy_letter":
                    return "letter_destroyed";
                case "intercept_letter":
                    return "player_intercepted";
                case "expose_truth":
                    return "truth_exposed";
                default:
                    return string.Empty;
            }
        }

        private void Finish(string status, string message, int exitCode)
        {
            var report = new SmokeReport
            {
                status = status,
                message = message,
                session_id = session?.SessionId ?? string.Empty,
                version = session?.State?.version ?? 0,
                resumed = resumeOnly,
                route = session?.LastSceneRun?.route ?? secretLetterRoute,
                ending = session?.LastSceneRun?.ending ?? string.Empty,
                world_package_id =
                    session?.LastSceneRun?.world_package_id ?? string.Empty,
                objective_satisfied =
                    session?.LastSceneRun?.objective_satisfied ?? false,
                memory_record_count =
                    session?.LastSceneRun?.memory_record_count ?? 0,
                presentation_sequence =
                    presentationDispatcher?.LastAcknowledgedSequence ?? 0L,
                presentation_commands =
                    presentationDispatcher?.DispatchedCommandCount ?? 0,
            };
            var json = JsonUtility.ToJson(report, true);
            if (!string.IsNullOrWhiteSpace(reportPath))
            {
                var fullPath = Path.GetFullPath(reportPath);
                var directory = Path.GetDirectoryName(fullPath);
                if (!string.IsNullOrWhiteSpace(directory))
                {
                    Directory.CreateDirectory(directory);
                }
                File.WriteAllText(fullPath, json);
            }
            Debug.Log(
                $"NOVELSIM_SMOKE_{status.ToUpperInvariant()} "
                + $"session={report.session_id} version={report.version}");
            Application.Quit(exitCode);
        }

        private static bool HasArgument(string[] args, string name)
        {
            return Array.Exists(
                args,
                item => string.Equals(
                    item,
                    name,
                    StringComparison.OrdinalIgnoreCase));
        }

        private static string ArgumentValue(string[] args, string name)
        {
            for (var index = 0; index + 1 < args.Length; index++)
            {
                if (string.Equals(
                    args[index],
                    name,
                    StringComparison.OrdinalIgnoreCase))
                {
                    return args[index + 1];
                }
            }
            return string.Empty;
        }

        [Serializable]
        private sealed class SmokeReport
        {
            public string status;
            public string message;
            public string session_id;
            public int version;
            public bool resumed;
            public string route;
            public string ending;
            public string world_package_id;
            public bool objective_satisfied;
            public int memory_record_count;
            public long presentation_sequence;
            public int presentation_commands;
        }
    }

    /// <summary>
    /// Produces a deterministic standalone preview for visual regression and
    /// human review. It is inactive unless -novelsim-capture is supplied.
    /// </summary>
    internal sealed class StandaloneVisualCaptureRunner : MonoBehaviour
    {
        private Transform player;
        private Transform npc;
        private string capturePath;

        public void Configure(Transform playerTransform, Transform npcTransform)
        {
            capturePath = ArgumentValue(
                Environment.GetCommandLineArgs(),
                "-novelsim-capture");
            if (string.IsNullOrWhiteSpace(capturePath))
            {
                return;
            }
            player = playerTransform;
            npc = npcTransform;
            StartCoroutine(Capture());
        }

        private IEnumerator Capture()
        {
            player.position = new Vector3(-0.45f, 0.08f, -3.6f);
            player.rotation = Quaternion.Euler(0f, 7f, 0f);
            if (npc != null)
            {
                npc.position = new Vector3(0.85f, 0.08f, 5.9f);
            }
            Physics.SyncTransforms();
            yield return new WaitForSecondsRealtime(3.2f);

            var fullPath = Path.GetFullPath(capturePath);
            var directory = Path.GetDirectoryName(fullPath);
            if (!string.IsNullOrWhiteSpace(directory))
            {
                Directory.CreateDirectory(directory);
            }
            var sceneCamera = Camera.main;
            if (sceneCamera == null)
            {
                Debug.LogError("NOVELSIM_VISUAL_CAPTURE camera missing");
                Application.Quit(7);
                yield break;
            }
            var renderTexture = new RenderTexture(
                1600,
                900,
                24,
                RenderTextureFormat.ARGB32);
            var texture = new Texture2D(
                1600,
                900,
                TextureFormat.RGB24,
                false);
            var previousTarget = sceneCamera.targetTexture;
            var previousActive = RenderTexture.active;
            sceneCamera.targetTexture = renderTexture;
            sceneCamera.Render();
            RenderTexture.active = renderTexture;
            texture.ReadPixels(new Rect(0f, 0f, 1600f, 900f), 0, 0);
            texture.Apply();
            File.WriteAllBytes(fullPath, texture.EncodeToPNG());
            sceneCamera.targetTexture = previousTarget;
            RenderTexture.active = previousActive;
            Destroy(texture);
            renderTexture.Release();
            Destroy(renderTexture);
            yield return null;
            Debug.Log($"NOVELSIM_VISUAL_CAPTURE {fullPath}");
            Application.Quit(File.Exists(fullPath) ? 0 : 7);
        }

        private static string ArgumentValue(string[] args, string name)
        {
            for (var index = 0; index + 1 < args.Length; index++)
            {
                if (string.Equals(
                    args[index],
                    name,
                    StringComparison.OrdinalIgnoreCase))
                {
                    return args[index + 1];
                }
            }
            return string.Empty;
        }
    }
}
