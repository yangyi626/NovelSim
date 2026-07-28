using UnityEngine;
using NovelSim.UI;
using NovelSim.World;
#if ENABLE_INPUT_SYSTEM
using UnityEngine.InputSystem;
#endif

namespace NovelSim.Interaction
{
    public sealed class PlayerInteractor : MonoBehaviour
    {
        [SerializeField]
        private float radius = 2.4f;

        private WorldSessionManager session;
        private NovelSimHud hud;
        private InteractionTarget current;

        public void Configure(WorldSessionManager manager, NovelSimHud targetHud)
        {
            session = manager;
            hud = targetHud;
        }

        private void Update()
        {
            var closest = FindClosestTarget();
            if (closest != current)
            {
                current = closest;
                hud?.SetInteractionHint(
                    current == null
                        ? string.Empty
                        : $"按 E 与 {current.DisplayName} 交互");
            }

            if (current != null && InteractionPressed())
            {
                session?.SubmitAction(current.ServerAction);
            }
        }

        private InteractionTarget FindClosestTarget()
        {
            InteractionTarget closest = null;
            var closestDistance = float.MaxValue;
            foreach (var hit in Physics.OverlapSphere(transform.position, radius))
            {
                var target = hit.GetComponentInParent<InteractionTarget>();
                if (target == null)
                {
                    continue;
                }
                var distance = (target.transform.position - transform.position)
                    .sqrMagnitude;
                if (distance < closestDistance)
                {
                    closest = target;
                    closestDistance = distance;
                }
            }
            return closest;
        }

        private static bool InteractionPressed()
        {
#if ENABLE_INPUT_SYSTEM
            return Keyboard.current != null
                && Keyboard.current.eKey.wasPressedThisFrame;
#elif ENABLE_LEGACY_INPUT_MANAGER
            return Input.GetKeyDown(KeyCode.E);
#else
            return false;
#endif
        }
    }
}
