using System;
using System.Collections;
using System.IO;
using UnityEngine;
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
            HuarongLaneVisualDirector.BuildEnvironment(transform);
            var npc = CreateNpc();
            var interactor = CreatePlayer(cameraTransform, session, hud);
            gameObject.AddComponent<StandaloneInteractionSmokeRunner>()
                .Configure(session, interactor, npc);
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
        }

        private InteractionTarget CreateNpc()
        {
            var npc = new GameObject("Lane Guard NPC");
            npc.name = "Lane Guard NPC";
            npc.transform.SetParent(transform);
            npc.transform.position = new Vector3(0.85f, 0.08f, 6f);
            npc.transform.rotation = Quaternion.Euler(0f, 198f, 0f);
            var collider = npc.AddComponent<CapsuleCollider>();
            collider.center = new Vector3(0f, 1.15f, 0f);
            collider.height = 2.3f;
            collider.radius = 0.52f;
            var target = npc.AddComponent<InteractionTarget>();
            target.Configure(
                "华容巷守卫",
                "走近守卫，询问华容巷里刚才发生了什么");
            HuarongLaneVisualDirector.BuildGuardVisual(npc.transform);
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
            controller.height = 2.35f;
            controller.radius = 0.5f;
            controller.center = new Vector3(0f, 1.16f, 0f);
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
        private const float TurnTimeout = 240f;

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
}
