using System.Collections;
using UnityEngine;
using NovelSim.Characters;
using NovelSim.Interaction;
using NovelSim.Network;
using NovelSim.UI;
using NovelSim.World;

namespace NovelSim.Core
{
    /// <summary>
    /// 首个竖切片不依赖美术资产：运行时生成地面、玩家、NPC、相机和 HUD。
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
            CreateFloor();
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
                return existing.transform;
            }
            var cameraObject = new GameObject("Main Camera");
            cameraObject.tag = "MainCamera";
            cameraObject.AddComponent<Camera>();
            cameraObject.AddComponent<AudioListener>();
            cameraObject.transform.position = new Vector3(0f, 4f, -6f);
            return cameraObject.transform;
        }

        private static void CreateLighting()
        {
            if (FindFirstObjectByType<Light>() != null)
            {
                return;
            }
            var lightObject = new GameObject("Sun");
            var light = lightObject.AddComponent<Light>();
            light.type = LightType.Directional;
            light.intensity = 1.2f;
            lightObject.transform.rotation = Quaternion.Euler(48f, -32f, 0f);
        }

        private static void CreateFloor()
        {
            var floor = GameObject.CreatePrimitive(PrimitiveType.Plane);
            floor.name = "Huarong Lane Ground";
            floor.transform.localScale = new Vector3(3f, 1f, 3f);
            SetColor(floor, new Color(0.22f, 0.24f, 0.28f));
        }

        private static void CreateNpc()
        {
            var npc = GameObject.CreatePrimitive(PrimitiveType.Capsule);
            npc.name = "Lane Guard NPC";
            npc.transform.position = new Vector3(0f, 1f, 5f);
            npc.AddComponent<InteractionTarget>().Configure(
                "华容巷守卫",
                "走近守卫，询问华容巷里刚才发生了什么");
            SetColor(npc, new Color(0.58f, 0.30f, 0.22f));
        }

        private static void CreatePlayer(
            Transform cameraTransform,
            WorldSessionManager session,
            NovelSimHud hud)
        {
            var player = GameObject.CreatePrimitive(PrimitiveType.Capsule);
            player.name = "Player";
            player.transform.position = new Vector3(0f, 1f, 0f);
            var primitiveCollider = player.GetComponent<CapsuleCollider>();
            primitiveCollider.enabled = false;
            Destroy(primitiveCollider);
            var controller = player.AddComponent<CharacterController>();
            controller.height = 2f;
            controller.radius = 0.45f;
            controller.center = Vector3.zero;
            player.AddComponent<ThirdPersonMotor>().Configure(cameraTransform);
            player.AddComponent<PlayerInteractor>().Configure(session, hud);
            SetColor(player, new Color(0.22f, 0.48f, 0.66f));
        }

        private static void SetColor(GameObject target, Color color)
        {
            var renderer = target.GetComponent<Renderer>();
            if (renderer != null)
            {
                renderer.material.color = color;
            }
        }
    }
}
