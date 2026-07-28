using System.Collections;
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
            api.Configure(PlayerPrefs.GetString(
                "NovelSim.ApiBaseUrl",
                DefaultApiUrl));
            var session = systems.AddComponent<WorldSessionManager>();
            session.Configure(api);
            var hud = systems.AddComponent<NovelSimHud>();
            hud.Configure(session, api);

            var cameraTransform = EnsureCamera();
            CreateLighting();
            HuarongLaneVisualDirector.BuildEnvironment(transform);
            CreateNpc();
            CreatePlayer(cameraTransform, session, hud);
            StartCoroutine(StartSessionNextFrame(session));
        }

        private static IEnumerator StartSessionNextFrame(
            WorldSessionManager session)
        {
            yield return null;
            session.StartNewSession();
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

        private void CreateNpc()
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
            npc.AddComponent<InteractionTarget>().Configure(
                "华容巷守卫",
                "走近守卫，询问华容巷里刚才发生了什么");
            HuarongLaneVisualDirector.BuildGuardVisual(npc.transform);
        }

        private void CreatePlayer(
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
            player.AddComponent<PlayerInteractor>().Configure(session, hud);
            HuarongLaneVisualDirector.BuildPlayerVisual(player.transform);
        }
    }
}
