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
        private float moveSpeed = 4.5f;

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
        private float verticalVelocity;
        private float cameraYaw;

        public void Configure(Transform targetCamera)
        {
            cameraTransform = targetCamera;
            cameraYaw = transform.eulerAngles.y;
        }

        private void Awake()
        {
            controller = GetComponent<CharacterController>();
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
            var motion = direction * moveSpeed;
            motion.y = verticalVelocity;
            controller.Move(motion * Time.deltaTime);
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
            var targetPosition =
                target - orbit * Vector3.forward * cameraDistance;
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
    }
}
