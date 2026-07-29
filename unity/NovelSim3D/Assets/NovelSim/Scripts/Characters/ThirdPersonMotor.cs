using UnityEngine;
#if ENABLE_INPUT_SYSTEM
using UnityEngine.InputSystem;
#endif

namespace NovelSim.Characters
{
    [RequireComponent(typeof(CharacterController))]
    public sealed class ThirdPersonMotor : MonoBehaviour
    {
        [SerializeField]
        private float moveSpeed = 4.2f;

        [SerializeField]
        private float sprintSpeed = 6.2f;

        [SerializeField]
        private float turnSpeed = 12f;

        [SerializeField]
        private float cameraDistance = 6.8f;

        [SerializeField]
        private float cameraHeight = 1.55f;

        [SerializeField]
        private float cameraPitch = 17f;

        private CharacterController controller;
        private Transform cameraTransform;
        private StylizedCharacterAnimator presentation;
        private float verticalVelocity;
        private float cameraYaw;
        private float normalizedSpeed;

        public float NormalizedSpeed => normalizedSpeed;

        public void Configure(Transform targetCamera)
        {
            cameraTransform = targetCamera;
            cameraYaw = transform.eulerAngles.y;
        }

        private void Awake()
        {
            controller = GetComponent<CharacterController>();
            presentation = GetComponent<StylizedCharacterAnimator>();
        }

        private void Update()
        {
            var input = ReadMoveInput();
            var forward = cameraTransform != null
                ? Vector3.ProjectOnPlane(cameraTransform.forward, Vector3.up).normalized
                : Vector3.forward;
            var right = cameraTransform != null
                ? Vector3.ProjectOnPlane(cameraTransform.right, Vector3.up).normalized
                : Vector3.right;
            var direction = Vector3.ClampMagnitude(
                forward * input.y + right * input.x,
                1f);
            var sprinting = direction.sqrMagnitude > 0.01f
                && ReadSprintInput();
            var currentSpeed = sprinting ? sprintSpeed : moveSpeed;
            normalizedSpeed = direction.magnitude
                * (sprinting ? 1f : moveSpeed / sprintSpeed);

            if (direction.sqrMagnitude > 0.001f)
            {
                transform.rotation = Quaternion.Slerp(
                    transform.rotation,
                    Quaternion.LookRotation(direction, Vector3.up),
                    turnSpeed * Time.deltaTime);
            }

            if (controller.isGrounded && verticalVelocity < 0f)
            {
                verticalVelocity = -2f;
            }
            verticalVelocity += Physics.gravity.y * Time.deltaTime;
            var motion = direction * currentSpeed;
            motion.y = verticalVelocity;
            controller.Move(motion * Time.deltaTime);
            if (presentation == null)
            {
                presentation = GetComponent<StylizedCharacterAnimator>();
            }
            presentation?.SetLocomotion(normalizedSpeed);
        }

        private void LateUpdate()
        {
            if (cameraTransform == null)
            {
                return;
            }

            var look = ReadLookInput();
            cameraYaw += look.x * 0.11f;
            cameraPitch = Mathf.Clamp(
                cameraPitch - look.y * 0.09f,
                8f,
                34f);
            cameraDistance = Mathf.Clamp(
                cameraDistance - ReadZoomInput() * 0.008f,
                4.5f,
                8.5f);

            var target = transform.position + Vector3.up * cameraHeight;
            var orbit = Quaternion.Euler(cameraPitch, cameraYaw, 0f);
            target += orbit * Vector3.right * 0.32f;
            var desiredPosition =
                target - orbit * Vector3.forward * cameraDistance;
            var targetPosition = ResolveCameraPosition(
                target,
                desiredPosition);
            cameraTransform.position = Vector3.Lerp(
                cameraTransform.position,
                targetPosition,
                8f * Time.deltaTime);
            cameraTransform.rotation = Quaternion.Slerp(
                cameraTransform.rotation,
                Quaternion.LookRotation(
                    target - cameraTransform.position,
                    Vector3.up),
                10f * Time.deltaTime);
            var sceneCamera = cameraTransform.GetComponent<Camera>();
            if (sceneCamera != null)
            {
                sceneCamera.fieldOfView = Mathf.Lerp(
                    sceneCamera.fieldOfView,
                    Mathf.Lerp(52f, 56f, normalizedSpeed),
                    Time.deltaTime * 4f);
            }
        }

        private Vector3 ResolveCameraPosition(
            Vector3 target,
            Vector3 desired)
        {
            var path = desired - target;
            var distance = path.magnitude;
            if (distance < 0.01f)
            {
                return desired;
            }
            var resolvedDistance = distance;
            foreach (var hit in Physics.SphereCastAll(
                target,
                0.22f,
                path / distance,
                distance,
                ~0,
                QueryTriggerInteraction.Ignore))
            {
                if (hit.collider == null
                    || hit.collider.transform.IsChildOf(transform))
                {
                    continue;
                }
                resolvedDistance = Mathf.Min(
                    resolvedDistance,
                    Mathf.Max(0.5f, hit.distance - 0.16f));
            }
            return target + path.normalized * resolvedDistance;
        }

        private static Vector2 ReadMoveInput()
        {
#if ENABLE_INPUT_SYSTEM
            var keyboard = Keyboard.current;
            if (keyboard == null)
            {
                return Vector2.zero;
            }
            return new Vector2(
                (keyboard.dKey.isPressed ? 1f : 0f)
                    - (keyboard.aKey.isPressed ? 1f : 0f),
                (keyboard.wKey.isPressed ? 1f : 0f)
                    - (keyboard.sKey.isPressed ? 1f : 0f));
#elif ENABLE_LEGACY_INPUT_MANAGER
            return new Vector2(
                Input.GetAxisRaw("Horizontal"),
                Input.GetAxisRaw("Vertical"));
#else
            return Vector2.zero;
#endif
        }

        private static Vector2 ReadLookInput()
        {
#if ENABLE_INPUT_SYSTEM
            var mouse = Mouse.current;
            return mouse != null && mouse.rightButton.isPressed
                ? mouse.delta.ReadValue()
                : Vector2.zero;
#elif ENABLE_LEGACY_INPUT_MANAGER
            return Input.GetMouseButton(1)
                ? new Vector2(
                    Input.GetAxis("Mouse X") * 10f,
                    Input.GetAxis("Mouse Y") * 10f)
                : Vector2.zero;
#else
            return Vector2.zero;
#endif
        }

        private static float ReadZoomInput()
        {
#if ENABLE_INPUT_SYSTEM
            return Mouse.current?.scroll.ReadValue().y ?? 0f;
#elif ENABLE_LEGACY_INPUT_MANAGER
            return Input.mouseScrollDelta.y * 120f;
#else
            return 0f;
#endif
        }

        private static bool ReadSprintInput()
        {
#if ENABLE_INPUT_SYSTEM
            var keyboard = Keyboard.current;
            return keyboard != null
                && (keyboard.leftShiftKey.isPressed
                    || keyboard.rightShiftKey.isPressed);
#elif ENABLE_LEGACY_INPUT_MANAGER
            return Input.GetKey(KeyCode.LeftShift)
                || Input.GetKey(KeyCode.RightShift);
#else
            return false;
#endif
        }
    }
}
