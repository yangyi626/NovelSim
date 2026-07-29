using UnityEngine;
using UnityEngine.AI;

namespace NovelSim.Characters
{
    /// <summary>
    /// Lightweight procedural locomotion for the generated articulated rig.
    /// It intentionally avoids animation clips so batch tests and standalone
    /// builds keep working before licensed production assets are introduced.
    /// </summary>
    public sealed class StylizedCharacterAnimator : MonoBehaviour
    {
        private Transform visualRoot;
        private Transform spine;
        private Transform head;
        private Transform leftArm;
        private Transform rightArm;
        private Transform leftLeg;
        private Transform rightLeg;
        private Transform focusMarker;
        private Quaternion leftArmBase;
        private Quaternion rightArmBase;
        private Quaternion leftLegBase;
        private Quaternion rightLegBase;
        private Vector3 visualBasePosition;
        private CharacterController characterController;
        private NavMeshAgent navMeshAgent;
        private float requestedSpeed;
        private float smoothedSpeed;
        private float gaitPhase;
        private float reactionRemaining;

        public bool IsArticulated =>
            visualRoot != null
            && leftArm != null
            && rightArm != null
            && leftLeg != null
            && rightLeg != null;

        public bool IsFocused =>
            focusMarker != null && focusMarker.gameObject.activeSelf;

        public float NormalizedSpeed => smoothedSpeed;

        public void Configure(
            Transform targetVisualRoot,
            Transform targetSpine,
            Transform targetHead,
            Transform targetLeftArm,
            Transform targetRightArm,
            Transform targetLeftLeg,
            Transform targetRightLeg,
            Transform targetFocusMarker)
        {
            visualRoot = targetVisualRoot;
            spine = targetSpine;
            head = targetHead;
            leftArm = targetLeftArm;
            rightArm = targetRightArm;
            leftLeg = targetLeftLeg;
            rightLeg = targetRightLeg;
            focusMarker = targetFocusMarker;
            visualBasePosition = visualRoot.localPosition;
            leftArmBase = leftArm.localRotation;
            rightArmBase = rightArm.localRotation;
            leftLegBase = leftLeg.localRotation;
            rightLegBase = rightLeg.localRotation;
            characterController = GetComponent<CharacterController>();
            navMeshAgent = GetComponent<NavMeshAgent>();
        }

        public void SetLocomotion(float normalizedSpeed)
        {
            requestedSpeed = Mathf.Clamp01(normalizedSpeed);
        }

        public void SetFocused(bool focused)
        {
            if (focusMarker != null)
            {
                focusMarker.gameObject.SetActive(focused);
            }
        }

        public void PlayInteractionReaction()
        {
            reactionRemaining = 0.75f;
        }

        private void Update()
        {
            if (!IsArticulated)
            {
                return;
            }

            var measuredSpeed = RequestedOrMeasuredSpeed();
            smoothedSpeed = Mathf.MoveTowards(
                smoothedSpeed,
                measuredSpeed,
                Time.deltaTime * 4.5f);
            gaitPhase += Time.deltaTime * Mathf.Lerp(
                1.7f,
                8.2f,
                smoothedSpeed);

            var walkSwing = Mathf.Sin(gaitPhase)
                * Mathf.Lerp(1.5f, 27f, smoothedSpeed);
            var strideLift = Mathf.Abs(Mathf.Sin(gaitPhase))
                * Mathf.Lerp(0f, 0.035f, smoothedSpeed);
            leftArm.localRotation = leftArmBase
                * Quaternion.Euler(walkSwing, 0f, 0f);
            rightArm.localRotation = rightArmBase
                * Quaternion.Euler(-walkSwing, 0f, 0f);
            leftLeg.localRotation = leftLegBase
                * Quaternion.Euler(-walkSwing * 0.72f, 0f, 0f);
            rightLeg.localRotation = rightLegBase
                * Quaternion.Euler(walkSwing * 0.72f, 0f, 0f);

            var breathing = Mathf.Sin(Time.time * 1.65f) * 0.008f;
            var reactionLift = 0f;
            if (reactionRemaining > 0f)
            {
                reactionRemaining = Mathf.Max(
                    0f,
                    reactionRemaining - Time.deltaTime);
                var progress = 1f - reactionRemaining / 0.75f;
                reactionLift = Mathf.Sin(progress * Mathf.PI) * 0.08f;
                head.localRotation = Quaternion.Euler(
                    Mathf.Sin(progress * Mathf.PI * 2f) * 5f,
                    0f,
                    0f);
            }
            else
            {
                head.localRotation = Quaternion.Euler(
                    Mathf.Sin(Time.time * 0.7f) * 1.2f,
                    Mathf.Sin(Time.time * 0.42f) * 2f,
                    0f);
            }

            visualRoot.localPosition = visualBasePosition + Vector3.up
                * (breathing + strideLift + reactionLift);
            spine.localRotation = Quaternion.Euler(
                0f,
                0f,
                Mathf.Sin(gaitPhase * 0.5f)
                    * Mathf.Lerp(0.4f, 2.1f, smoothedSpeed));

            if (focusMarker != null && focusMarker.gameObject.activeSelf)
            {
                focusMarker.Rotate(
                    Vector3.up,
                    75f * Time.deltaTime,
                    Space.Self);
                var pulse = 1f + Mathf.Sin(Time.time * 5f) * 0.12f;
                focusMarker.localScale = Vector3.one * pulse;
            }
        }

        private float RequestedOrMeasuredSpeed()
        {
            if (requestedSpeed > 0.001f)
            {
                return requestedSpeed;
            }
            if (characterController != null)
            {
                return Mathf.Clamp01(
                    new Vector2(
                        characterController.velocity.x,
                        characterController.velocity.z).magnitude / 4.5f);
            }
            if (navMeshAgent != null
                && navMeshAgent.enabled
                && navMeshAgent.isOnNavMesh)
            {
                return Mathf.Clamp01(
                    navMeshAgent.velocity.magnitude
                    / Mathf.Max(0.1f, navMeshAgent.speed));
            }
            return 0f;
        }

        private void LateUpdate()
        {
            requestedSpeed = 0f;
        }
    }
}
