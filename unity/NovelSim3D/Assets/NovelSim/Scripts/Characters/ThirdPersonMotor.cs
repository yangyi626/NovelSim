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
        private Vector3 cameraOffset = new Vector3(0f, 3.6f, -6f);

        private CharacterController controller;
        private Transform cameraTransform;
        private float verticalVelocity;

        public void Configure(Transform targetCamera)
        {
            cameraTransform = targetCamera;
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
            var targetPosition = transform.TransformPoint(cameraOffset);
            cameraTransform.position = Vector3.Lerp(
                cameraTransform.position,
                targetPosition,
                10f * Time.deltaTime);
            cameraTransform.LookAt(transform.position + Vector3.up * 1.2f);
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
    }
}
