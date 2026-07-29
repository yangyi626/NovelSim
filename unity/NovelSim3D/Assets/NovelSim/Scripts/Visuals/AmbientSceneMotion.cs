using UnityEngine;

namespace NovelSim.Visuals
{
    public sealed class AmbientSway : MonoBehaviour
    {
        private Quaternion baseRotation;
        private Vector3 axis = Vector3.forward;
        private float angle = 2f;
        private float speed = 1f;
        private float phase;

        public void Configure(
            Vector3 swayAxis,
            float maxAngle,
            float swaySpeed,
            float phaseOffset)
        {
            baseRotation = transform.localRotation;
            axis = swayAxis.sqrMagnitude < 0.01f
                ? Vector3.forward
                : swayAxis.normalized;
            angle = Mathf.Max(0f, maxAngle);
            speed = Mathf.Max(0.01f, swaySpeed);
            phase = phaseOffset;
        }

        private void Awake()
        {
            baseRotation = transform.localRotation;
        }

        private void Update()
        {
            var offset = Mathf.Sin(Time.time * speed + phase) * angle;
            transform.localRotation = baseRotation
                * Quaternion.AngleAxis(offset, axis);
        }
    }

    [RequireComponent(typeof(Light))]
    public sealed class LanternFlicker : MonoBehaviour
    {
        private Light lanternLight;
        private float baseIntensity;
        private float phase;

        public void Configure(float intensity, float phaseOffset)
        {
            lanternLight = GetComponent<Light>();
            baseIntensity = intensity;
            phase = phaseOffset;
        }

        private void Awake()
        {
            lanternLight = GetComponent<Light>();
            baseIntensity = lanternLight.intensity;
        }

        private void Update()
        {
            var noise = Mathf.PerlinNoise(
                phase,
                Time.time * 1.8f + phase);
            var pulse = Mathf.Sin(Time.time * 7f + phase) * 0.035f;
            lanternLight.intensity = baseIntensity
                * (0.88f + noise * 0.18f + pulse);
        }
    }
}
