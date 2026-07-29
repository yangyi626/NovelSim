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
            gameObject.AddComponent<StandaloneInteractionSmokeRunner>()
                .Configure(session, interactor, npc);
            gameObject.AddComponent<StandaloneVisualCaptureRunner>()
                .Configure(interactor.transform, npc.transform);
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
            light.color = new Color(0.43f, 0.58f, 0.78f);
            light.intensity = 0.86f;
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
            fill.intensity = 2.8f;
            fill.range = 8.5f;
            fill.shadows = LightShadows.None;

            var rimObject = new GameObject("Rain Blue Rim");
            rimObject.transform.SetParent(transform);
            rimObject.transform.position = new Vector3(2f, 5.5f, 11f);
            rimObject.transform.rotation = Quaternion.Euler(42f, 194f, 0f);
            var rim = rimObject.AddComponent<Light>();
            rim.type = LightType.Spot;
            rim.color = new Color(0.2f, 0.5f, 0.82f);
            rim.intensity = 3.2f;
            rim.range = 17f;
            rim.spotAngle = 58f;
            rim.innerSpotAngle = 32f;
            rim.shadows = LightShadows.Soft;
        }

        private InteractionTarget CreateNpc(Transform player)
        {
            var npc = new GameObject("Lane Guard NPC");
            npc.name = "Lane Guard NPC";
            npc.transform.SetParent(transform);
            npc.transform.position = new Vector3(0.85f, 0.08f, 6f);
            npc.transform.rotation = Quaternion.Euler(0f, 198f, 0f);
            var collider = npc.AddComponent<CapsuleCollider>();
            collider.center = new Vector3(0f, 1.36f, 0f);
            collider.height = 2.72f;
            collider.radius = 0.52f;
            var target = npc.AddComponent<InteractionTarget>();
            target.Configure(
                "华容巷守卫",
                "走近守卫，询问华容巷里刚才发生了什么");
            HuarongLaneVisualDirector.BuildGuardVisual(npc.transform);
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

        private WorldSessionManager session;
        private PlayerInteractor interactor;
        private InteractionTarget target;
        private bool enabledRunner;
        private bool resumeOnly;
        private string reportPath;

        public void Configure(
            WorldSessionManager manager,
            PlayerInteractor playerInteractor,
            InteractionTarget interactionTarget)
        {
            session = manager;
            interactor = playerInteractor;
            target = interactionTarget;
            var args = Environment.GetCommandLineArgs();
            enabledRunner = HasArgument(args, "-novelsim-smoke-interact")
                || HasArgument(args, "-novelsim-smoke-resume");
            if (!enabledRunner)
            {
                return;
            }
            resumeOnly = HasArgument(args, "-novelsim-smoke-resume");
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
                Finish("resume_ok", "存档恢复成功", 0);
                yield break;
            }

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
            Finish("interaction_ok", "真实 E 交互完成", 0);
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
            ScreenCapture.CaptureScreenshot(fullPath, 1);
            var started = Time.realtimeSinceStartup;
            while (
                (!File.Exists(fullPath)
                    || new FileInfo(fullPath).Length == 0)
                && Time.realtimeSinceStartup - started < 12f)
            {
                yield return null;
            }
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
