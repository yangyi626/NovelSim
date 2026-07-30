using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Rendering;

namespace NovelSim.Visuals
{
    /// <summary>
    /// Builds a stylized, asset-free Huarong Lane art pass at runtime.
    /// The geometry stays deliberately lightweight so the vertical slice can
    /// be cloned and tested without downloading third-party art packages.
    /// </summary>
    public static class HuarongLaneVisualDirector
    {
        private static readonly Dictionary<string, Material> Materials = new();

        public static Transform BuildEnvironment(Transform parent)
        {
            ConfigureAtmosphere();

            var root = new GameObject("Huarong Lane Art");
            root.transform.SetParent(parent, false);

            CreateRoad(root.transform);
            CreateArchitecture(root.transform);
            CreateGate(root.transform);
            CreateLanterns(root.transform);
            CreateStreetProps(root.transform);
            CreateRain(root.transform);
            CreateGroundMist(root.transform);
            CreateMoonBackdrop(root.transform);
            return root.transform;
        }

        public static NovelSim.Characters.StylizedCharacterAnimator
            BuildPlayerVisual(Transform root)
        {
            return StylizedCharacterFactory.BuildPlayer(root);
        }

        public static NovelSim.Characters.StylizedCharacterAnimator
            BuildGuardVisual(Transform root)
        {
            return StylizedCharacterFactory.BuildGuard(root);
        }

        public static NovelSim.Characters.StylizedCharacterAnimator
            BuildQingqingVisual(Transform root)
        {
            return StylizedCharacterFactory.BuildQingqing(root);
        }

        private static void ConfigureAtmosphere()
        {
            RenderSettings.skybox = null;
            RenderSettings.fog = true;
            RenderSettings.fogMode = FogMode.ExponentialSquared;
            RenderSettings.fogDensity = 0.009f;
            RenderSettings.fogColor = new Color(0.035f, 0.075f, 0.11f);
            RenderSettings.ambientMode = AmbientMode.Trilight;
            RenderSettings.ambientSkyColor =
                new Color(0.18f, 0.26f, 0.35f);
            RenderSettings.ambientEquatorColor =
                new Color(0.085f, 0.13f, 0.175f);
            RenderSettings.ambientGroundColor =
                new Color(0.035f, 0.052f, 0.072f);
            RenderSettings.reflectionIntensity = 0.52f;
        }

        private static void CreateRoad(Transform parent)
        {
            var foundation = Material(
                "Lane Foundation",
                new Color(0.025f, 0.035f, 0.045f),
                0.82f,
                new Color(0.002f, 0.006f, 0.01f));
            var stoneA = Material(
                "Wet Stone A",
                new Color(0.15f, 0.21f, 0.26f),
                0.9f,
                new Color(0.006f, 0.012f, 0.018f));
            var stoneB = Material(
                "Wet Stone B",
                new Color(0.18f, 0.23f, 0.27f),
                0.86f);
            var puddle = Material(
                "Rain Puddle",
                new Color(0.025f, 0.11f, 0.16f),
                1f,
                new Color(0.008f, 0.035f, 0.055f),
                0.18f);

            Primitive(
                PrimitiveType.Cube,
                "Lane Ground",
                parent,
                new Vector3(0f, -0.22f, 4f),
                new Vector3(10f, 0.42f, 34f),
                Quaternion.identity,
                foundation,
                true);

            for (var z = -11; z <= 19; z += 2)
            {
                for (var x = -3; x <= 3; x++)
                {
                    var alternate = (x + z / 2) % 2 == 0;
                    var offset = alternate ? 0.07f : -0.06f;
                    Primitive(
                        PrimitiveType.Cube,
                        $"Wet Stone {x} {z}",
                        parent,
                        new Vector3(
                            x * 1.28f + offset,
                            0.025f + Mathf.Abs(x % 2) * 0.006f,
                            z + (x % 2) * 0.15f),
                        new Vector3(1.18f, 0.06f, 1.72f),
                        Quaternion.Euler(0f, alternate ? 1.5f : -1f, 0f),
                        alternate ? stoneA : stoneB);
                }
            }

            CreatePuddle(
                parent,
                new Vector3(-1.9f, 0.07f, -3f),
                new Vector3(1.5f, 0.015f, 0.62f),
                puddle);
            CreatePuddle(
                parent,
                new Vector3(2.2f, 0.07f, 7.5f),
                new Vector3(1.25f, 0.015f, 0.5f),
                puddle);
            CreatePuddle(
                parent,
                new Vector3(-1.1f, 0.07f, 13f),
                new Vector3(1.8f, 0.015f, 0.55f),
                puddle);
        }

        private static void CreateArchitecture(Transform parent)
        {
            var plaster = Material(
                "Rain Darkened Plaster",
                new Color(0.16f, 0.17f, 0.18f),
                0.28f);
            var plasterAlt = Material(
                "Old Plaster",
                new Color(0.2f, 0.19f, 0.17f),
                0.22f);
            var timber = Material(
                "Dark Timber",
                new Color(0.055f, 0.025f, 0.018f),
                0.42f);
            var tile = Material(
                "Blue Black Roof Tile",
                new Color(0.025f, 0.045f, 0.06f),
                0.72f,
                new Color(0.002f, 0.006f, 0.01f),
                0.18f);
            var window = Material(
                "Warm Paper Window",
                new Color(0.54f, 0.27f, 0.09f),
                0.28f,
                new Color(0.9f, 0.28f, 0.055f));

            for (var index = 0; index < 7; index++)
            {
                var z = -10f + index * 4.8f;
                CreateHouse(
                    parent,
                    -5.8f,
                    z,
                    true,
                    index % 2 == 0 ? plaster : plasterAlt,
                    timber,
                    tile,
                    window,
                    index % 3 != 1);
                CreateHouse(
                    parent,
                    5.8f,
                    z + 1.7f,
                    false,
                    index % 2 == 0 ? plasterAlt : plaster,
                    timber,
                    tile,
                    window,
                    index % 3 == 1);
            }
        }

        private static void CreateHouse(
            Transform parent,
            float x,
            float z,
            bool facesRight,
            Material wall,
            Material timber,
            Material tile,
            Material window,
            bool litWindow)
        {
            var house = new GameObject($"Old House {x:0} {z:0}");
            house.transform.SetParent(parent, false);
            var facadeX = x + (facesRight ? 1.1f : -1.1f);

            Primitive(
                PrimitiveType.Cube,
                "Wall",
                house.transform,
                new Vector3(x, 2.05f, z),
                new Vector3(2.3f, 4.1f, 4.55f),
                Quaternion.identity,
                wall,
                true);
            Primitive(
                PrimitiveType.Cube,
                "Eave",
                house.transform,
                new Vector3(facadeX, 4.08f, z),
                new Vector3(0.28f, 0.24f, 5.05f),
                Quaternion.identity,
                timber);
            Primitive(
                PrimitiveType.Cube,
                "Roof Inner",
                house.transform,
                new Vector3(x - 0.6f, 4.45f, z),
                new Vector3(1.75f, 0.18f, 5.15f),
                Quaternion.Euler(0f, 0f, facesRight ? -17f : 17f),
                tile);
            Primitive(
                PrimitiveType.Cube,
                "Roof Outer",
                house.transform,
                new Vector3(x + 0.6f, 4.45f, z),
                new Vector3(1.75f, 0.18f, 5.15f),
                Quaternion.Euler(0f, 0f, facesRight ? 17f : -17f),
                tile);

            for (var frame = -1; frame <= 1; frame++)
            {
                Primitive(
                    PrimitiveType.Cube,
                    $"Timber Frame {frame}",
                    house.transform,
                    new Vector3(facadeX, 2.05f, z + frame * 1.55f),
                    new Vector3(0.16f, 3.75f, 0.16f),
                    Quaternion.identity,
                    timber);
            }
            Primitive(
                PrimitiveType.Cube,
                "Timber Cross Beam",
                house.transform,
                new Vector3(facadeX, 2.86f, z),
                new Vector3(0.18f, 0.16f, 4.45f),
                Quaternion.identity,
                timber);

            var windowPosition = new Vector3(
                facadeX + (facesRight ? 0.03f : -0.03f),
                2.05f,
                z);
            Primitive(
                PrimitiveType.Cube,
                "Paper Window",
                house.transform,
                windowPosition,
                new Vector3(0.11f, 1.25f, 1.25f),
                Quaternion.identity,
                litWindow ? window : timber);
        }

        private static void CreateGate(Transform parent)
        {
            var redWood = Material(
                "Gate Vermilion",
                new Color(0.28f, 0.025f, 0.022f),
                0.38f);
            var roof = Material(
                "Gate Roof",
                new Color(0.018f, 0.032f, 0.045f),
                0.78f);
            var gold = Material(
                "Weathered Gold",
                new Color(0.55f, 0.3f, 0.07f),
                0.65f,
                new Color(0.025f, 0.009f, 0.001f),
                0.55f);

            var root = new GameObject("Huarong Lane Gate");
            root.transform.SetParent(parent, false);
            Primitive(
                PrimitiveType.Cylinder,
                "Left Gate Pillar",
                root.transform,
                new Vector3(-3.85f, 2.1f, 16f),
                new Vector3(0.34f, 2.1f, 0.34f),
                Quaternion.identity,
                redWood,
                true);
            Primitive(
                PrimitiveType.Cylinder,
                "Right Gate Pillar",
                root.transform,
                new Vector3(3.85f, 2.1f, 16f),
                new Vector3(0.34f, 2.1f, 0.34f),
                Quaternion.identity,
                redWood,
                true);
            Primitive(
                PrimitiveType.Cube,
                "Gate Beam",
                root.transform,
                new Vector3(0f, 4.05f, 16f),
                new Vector3(8.6f, 0.42f, 0.5f),
                Quaternion.identity,
                redWood);
            Primitive(
                PrimitiveType.Cube,
                "Gate Roof Left",
                root.transform,
                new Vector3(-2.15f, 4.48f, 16f),
                new Vector3(4.75f, 0.22f, 1.15f),
                Quaternion.Euler(0f, 0f, 8f),
                roof);
            Primitive(
                PrimitiveType.Cube,
                "Gate Roof Right",
                root.transform,
                new Vector3(2.15f, 4.48f, 16f),
                new Vector3(4.75f, 0.22f, 1.15f),
                Quaternion.Euler(0f, 0f, -8f),
                roof);
            Primitive(
                PrimitiveType.Cube,
                "Huarong Plaque",
                root.transform,
                new Vector3(0f, 3.68f, 15.72f),
                new Vector3(2.5f, 0.74f, 0.12f),
                Quaternion.identity,
                gold);
        }

        private static void CreateLanterns(Transform parent)
        {
            var red = Material(
                "Lantern Red",
                new Color(0.56f, 0.025f, 0.018f),
                0.4f,
                new Color(1.25f, 0.11f, 0.025f));
            var gold = Material(
                "Lantern Gold",
                new Color(0.62f, 0.31f, 0.055f),
                0.6f);
            var dark = Material(
                "Lantern Frame",
                new Color(0.035f, 0.018f, 0.012f),
                0.45f);

            var positions = new[]
            {
                new Vector3(-4.25f, 2.75f, -7f),
                new Vector3(4.25f, 2.75f, -1f),
                new Vector3(-4.25f, 2.75f, 5f),
                new Vector3(4.25f, 2.75f, 11f),
                new Vector3(-3.35f, 3.35f, 15.65f),
                new Vector3(3.35f, 3.35f, 15.65f),
            };
            for (var index = 0; index < positions.Length; index++)
            {
                var lantern = new GameObject($"Lantern {index + 1}");
                lantern.transform.SetParent(parent, false);
                lantern.transform.localPosition = positions[index];
                lantern.AddComponent<AmbientSway>().Configure(
                    new Vector3(0.3f, 0f, 1f),
                    index >= 4 ? 1.5f : 2.8f,
                    0.75f + index * 0.07f,
                    index * 1.13f);
                Primitive(
                    PrimitiveType.Cylinder,
                    "Glow",
                    lantern.transform,
                    Vector3.zero,
                    new Vector3(0.28f, 0.38f, 0.28f),
                    Quaternion.identity,
                    red);
                Primitive(
                    PrimitiveType.Cylinder,
                    "Top",
                    lantern.transform,
                    new Vector3(0f, 0.43f, 0f),
                    new Vector3(0.34f, 0.045f, 0.34f),
                    Quaternion.identity,
                    gold);
                Primitive(
                    PrimitiveType.Cylinder,
                    "Bottom",
                    lantern.transform,
                    new Vector3(0f, -0.43f, 0f),
                    new Vector3(0.34f, 0.045f, 0.34f),
                    Quaternion.identity,
                    dark);
                Primitive(
                    PrimitiveType.Cylinder,
                    "Tassel",
                    lantern.transform,
                    new Vector3(0f, -0.72f, 0f),
                    new Vector3(0.035f, 0.26f, 0.035f),
                    Quaternion.identity,
                    red);

                var light = lantern.AddComponent<Light>();
                light.type = LightType.Point;
                light.color = new Color(1f, 0.23f, 0.075f);
                light.intensity = index >= 4 ? 5.4f : 4.2f;
                light.range = index >= 4 ? 9f : 7.5f;
                light.shadows = LightShadows.Soft;
                light.shadowStrength = 0.55f;
                lantern.AddComponent<LanternFlicker>().Configure(
                    light.intensity,
                    index * 0.73f);
            }
        }

        private static void CreateStreetProps(Transform parent)
        {
            var wood = Material(
                "Prop Wood",
                new Color(0.11f, 0.052f, 0.025f),
                0.32f);
            var bamboo = Material(
                "Rain Bamboo",
                new Color(0.035f, 0.16f, 0.105f),
                0.38f);
            var banner = Material(
                "Wine Banner",
                new Color(0.38f, 0.035f, 0.028f),
                0.28f);

            Primitive(
                PrimitiveType.Cube,
                "Crate A",
                parent,
                new Vector3(-3.8f, 0.5f, 1.2f),
                new Vector3(0.9f, 0.9f, 0.9f),
                Quaternion.Euler(0f, 12f, 0f),
                wood,
                true);
            Primitive(
                PrimitiveType.Cube,
                "Crate B",
                parent,
                new Vector3(-3.6f, 1.25f, 1.35f),
                new Vector3(0.65f, 0.62f, 0.65f),
                Quaternion.Euler(0f, -9f, 0f),
                wood,
                true);
            Primitive(
                PrimitiveType.Cylinder,
                "Rain Barrel",
                parent,
                new Vector3(3.75f, 0.65f, 8.8f),
                new Vector3(0.55f, 0.65f, 0.55f),
                Quaternion.identity,
                wood,
                true);

            for (var index = 0; index < 6; index++)
            {
                var x = index < 3 ? -4.2f : 4.25f;
                var z = index < 3
                    ? 9.5f + index * 0.42f
                    : -5.5f + (index - 3) * 0.42f;
                var height = 2.6f + (index % 2) * 0.55f;
                Primitive(
                    PrimitiveType.Cylinder,
                    $"Bamboo {index}",
                    parent,
                    new Vector3(x, height * 0.5f, z),
                    new Vector3(0.065f, height * 0.5f, 0.065f),
                    Quaternion.Euler(index % 2 == 0 ? 3f : -4f, 0f, 0f),
                    bamboo);
                for (var leaf = 0; leaf < 3; leaf++)
                {
                    Primitive(
                        PrimitiveType.Sphere,
                        $"Bamboo Leaf {index} {leaf}",
                        parent,
                        new Vector3(
                            x + (leaf % 2 == 0 ? 0.18f : -0.2f),
                            1.45f + leaf * 0.52f,
                            z),
                        new Vector3(0.12f, 0.42f, 0.06f),
                        Quaternion.Euler(0f, 0f, leaf % 2 == 0 ? 48f : -48f),
                        bamboo);
                }
            }

            var bannerObject = Primitive(
                PrimitiveType.Cube,
                "Hanging Story Banner",
                parent,
                new Vector3(4.32f, 2.4f, 3.5f),
                new Vector3(0.08f, 2.25f, 0.95f),
                Quaternion.Euler(0f, 0f, -3f),
                banner);
            bannerObject.AddComponent<AmbientSway>().Configure(
                Vector3.forward,
                3.5f,
                0.62f,
                1.7f);
        }

        private static void CreateRain(Transform parent)
        {
            var rainObject = new GameObject("Rain Field");
            rainObject.transform.SetParent(parent, false);
            rainObject.transform.localPosition = new Vector3(0f, 11f, 4f);
            var particles = rainObject.AddComponent<ParticleSystem>();
            particles.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear);

            var main = particles.main;
            main.loop = true;
            main.duration = 2f;
            main.startLifetime = new ParticleSystem.MinMaxCurve(0.65f, 1.1f);
            main.startSpeed = new ParticleSystem.MinMaxCurve(17f, 23f);
            main.startSize = new ParticleSystem.MinMaxCurve(0.018f, 0.034f);
            main.startColor = new Color(0.48f, 0.72f, 0.92f, 0.36f);
            main.gravityModifier = 0.08f;
            main.simulationSpace = ParticleSystemSimulationSpace.World;
            main.maxParticles = 1800;

            var emission = particles.emission;
            emission.rateOverTime = 760f;

            var shape = particles.shape;
            shape.shapeType = ParticleSystemShapeType.Box;
            shape.scale = new Vector3(11f, 1f, 34f);
            shape.rotation = new Vector3(7f, 0f, 0f);

            var renderer = rainObject.GetComponent<ParticleSystemRenderer>();
            renderer.renderMode = ParticleSystemRenderMode.Stretch;
            renderer.velocityScale = 0.06f;
            renderer.lengthScale = 1.45f;
            renderer.sharedMaterial = ParticleMaterial();
            particles.Play();
        }

        private static void CreateGroundMist(Transform parent)
        {
            var mistObject = new GameObject("Ground Mist");
            mistObject.transform.SetParent(parent, false);
            mistObject.transform.localPosition = new Vector3(0f, 0.32f, 5f);
            var particles = mistObject.AddComponent<ParticleSystem>();
            particles.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear);

            var main = particles.main;
            main.loop = true;
            main.duration = 9f;
            main.startLifetime = new ParticleSystem.MinMaxCurve(7f, 12f);
            main.startSpeed = new ParticleSystem.MinMaxCurve(0.08f, 0.22f);
            main.startSize = new ParticleSystem.MinMaxCurve(2.2f, 4.8f);
            main.startColor = new ParticleSystem.MinMaxGradient(
                new Color(0.15f, 0.28f, 0.36f, 0.035f),
                new Color(0.28f, 0.38f, 0.44f, 0.07f));
            main.simulationSpace = ParticleSystemSimulationSpace.World;
            main.maxParticles = 70;

            var emission = particles.emission;
            emission.rateOverTime = 4.5f;

            var shape = particles.shape;
            shape.shapeType = ParticleSystemShapeType.Box;
            shape.scale = new Vector3(9f, 0.3f, 31f);

            var velocity = particles.velocityOverLifetime;
            velocity.enabled = true;
            velocity.x = new ParticleSystem.MinMaxCurve(-0.08f, 0.12f);
            velocity.y = new ParticleSystem.MinMaxCurve(0f, 0f);
            velocity.z = new ParticleSystem.MinMaxCurve(0.03f, 0.12f);

            var color = particles.colorOverLifetime;
            color.enabled = true;
            var gradient = new Gradient();
            gradient.SetKeys(
                new[]
                {
                    new GradientColorKey(
                        new Color(0.16f, 0.24f, 0.3f),
                        0f),
                    new GradientColorKey(
                        new Color(0.32f, 0.38f, 0.42f),
                        0.5f),
                    new GradientColorKey(
                        new Color(0.14f, 0.2f, 0.25f),
                        1f),
                },
                new[]
                {
                    new GradientAlphaKey(0f, 0f),
                    new GradientAlphaKey(0.7f, 0.32f),
                    new GradientAlphaKey(0f, 1f),
                });
            color.color = gradient;

            var renderer = mistObject.GetComponent<ParticleSystemRenderer>();
            renderer.renderMode = ParticleSystemRenderMode.Billboard;
            renderer.sortingFudge = -8f;
            renderer.sharedMaterial = MistMaterial();
            particles.Play();
        }

        private static void CreateMoonBackdrop(Transform parent)
        {
            var moon = Material(
                "Rain Veiled Moon",
                new Color(0.56f, 0.67f, 0.72f),
                0.78f,
                new Color(0.22f, 0.34f, 0.42f));
            Primitive(
                PrimitiveType.Sphere,
                "Veiled Moon",
                parent,
                new Vector3(-11f, 15f, 32f),
                new Vector3(4.2f, 4.2f, 1.2f),
                Quaternion.identity,
                moon);

            var silhouette = Material(
                "Distant Roof Silhouette",
                new Color(0.012f, 0.025f, 0.036f),
                0.2f);
            for (var index = 0; index < 5; index++)
            {
                var x = -11f + index * 5.5f;
                Primitive(
                    PrimitiveType.Cube,
                    $"Distant Roof {index + 1}",
                    parent,
                    new Vector3(x, 3.2f + index % 2, 29f + index),
                    new Vector3(5.8f, 0.32f, 2.4f),
                    Quaternion.Euler(0f, 0f, index % 2 == 0 ? 8f : -8f),
                    silhouette);
            }
        }

        private static void CreatePuddle(
            Transform parent,
            Vector3 position,
            Vector3 scale,
            Material material)
        {
            Primitive(
                PrimitiveType.Cylinder,
                "Puddle",
                parent,
                position,
                scale,
                Quaternion.identity,
                material);
        }

        private static void CreateArm(
            Transform parent,
            string name,
            Vector3 position,
            float angle,
            Material material)
        {
            Primitive(
                PrimitiveType.Capsule,
                name,
                parent,
                position,
                new Vector3(0.17f, 0.39f, 0.17f),
                Quaternion.Euler(0f, 0f, angle),
                material);
        }

        private static GameObject Primitive(
            PrimitiveType type,
            string name,
            Transform parent,
            Vector3 position,
            Vector3 scale,
            Quaternion rotation,
            Material material,
            bool keepCollider = false)
        {
            var target = GameObject.CreatePrimitive(type);
            target.name = name;
            target.transform.SetParent(parent, false);
            target.transform.localPosition = position;
            target.transform.localScale = scale;
            target.transform.localRotation = rotation;

            var renderer = target.GetComponent<Renderer>();
            if (renderer != null)
            {
                renderer.sharedMaterial = material;
                renderer.shadowCastingMode = ShadowCastingMode.On;
                renderer.receiveShadows = true;
            }
            if (!keepCollider)
            {
                var collider = target.GetComponent<Collider>();
                if (collider != null)
                {
                    Object.Destroy(collider);
                }
            }
            return target;
        }

        private static Material Material(
            string name,
            Color color,
            float smoothness,
            Color emission = default,
            float metallic = 0f)
        {
            if (Materials.TryGetValue(name, out var existing))
            {
                return existing;
            }

            var shader = Shader.Find("Universal Render Pipeline/Lit")
                ?? Shader.Find("Standard")
                ?? Shader.Find("Sprites/Default");
            var material = new Material(shader)
            {
                name = name,
                hideFlags = HideFlags.DontSave,
            };
            SetMaterialColor(material, color);
            if (material.HasProperty("_Smoothness"))
            {
                material.SetFloat("_Smoothness", smoothness);
            }
            if (material.HasProperty("_Metallic"))
            {
                material.SetFloat("_Metallic", metallic);
            }
            if (emission.maxColorComponent > 0f
                && material.HasProperty("_EmissionColor"))
            {
                material.SetColor("_EmissionColor", emission);
                material.EnableKeyword("_EMISSION");
            }
            Materials[name] = material;
            return material;
        }

        private static Material ParticleMaterial()
        {
            const string name = "Rain Streak";
            if (Materials.TryGetValue(name, out var existing))
            {
                return existing;
            }
            var shader = Shader.Find(
                "Universal Render Pipeline/Particles/Unlit")
                ?? Shader.Find("Particles/Standard Unlit")
                ?? Shader.Find("Sprites/Default");
            var material = new Material(shader)
            {
                name = name,
                hideFlags = HideFlags.DontSave,
            };
            SetMaterialColor(
                material,
                new Color(0.42f, 0.67f, 0.9f, 0.32f));
            Materials[name] = material;
            return material;
        }

        private static Material MistMaterial()
        {
            const string name = "Ground Mist Material";
            if (Materials.TryGetValue(name, out var existing))
            {
                return existing;
            }
            var shader = Shader.Find(
                "Universal Render Pipeline/Particles/Unlit")
                ?? Shader.Find("Particles/Standard Unlit")
                ?? Shader.Find("Sprites/Default");
            var material = new Material(shader)
            {
                name = name,
                hideFlags = HideFlags.DontSave,
            };
            SetMaterialColor(
                material,
                new Color(0.42f, 0.56f, 0.62f, 0.18f));
            Materials[name] = material;
            return material;
        }

        private static void SetMaterialColor(Material material, Color color)
        {
            if (material.HasProperty("_BaseColor"))
            {
                material.SetColor("_BaseColor", color);
            }
            if (material.HasProperty("_Color"))
            {
                material.SetColor("_Color", color);
            }
        }
    }
}
