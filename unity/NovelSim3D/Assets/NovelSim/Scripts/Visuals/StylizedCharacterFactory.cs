using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Rendering;
using NovelSim.Characters;

namespace NovelSim.Visuals
{
    /// <summary>
    /// Builds lightweight articulated characters without external art
    /// dependencies. The generated rig has a readable silhouette, facial
    /// features and separate limb pivots that can be animated at runtime.
    /// </summary>
    public static class StylizedCharacterFactory
    {
        private static readonly Dictionary<string, Material> Materials = new();

        public static StylizedCharacterAnimator BuildPlayer(Transform root)
        {
            var palette = new CharacterPalette
            {
                cloth = Material(
                    "Hero Deep Teal",
                    new Color(0.035f, 0.21f, 0.27f),
                    0.38f),
                clothAccent = Material(
                    "Hero Jade Edge",
                    new Color(0.07f, 0.5f, 0.52f),
                    0.48f,
                    new Color(0.006f, 0.045f, 0.045f)),
                accent = Material(
                    "Hero Crimson Sash",
                    new Color(0.55f, 0.035f, 0.055f),
                    0.32f),
                skin = Material(
                    "Hero Warm Skin",
                    new Color(0.83f, 0.62f, 0.5f),
                    0.42f),
                hair = Material(
                    "Hero Ink Hair",
                    new Color(0.006f, 0.009f, 0.014f),
                    0.56f),
                eye = Material(
                    "Hero Eyes",
                    new Color(0.008f, 0.012f, 0.016f),
                    0.7f),
                metal = Material(
                    "Hero Silver",
                    new Color(0.34f, 0.4f, 0.43f),
                    0.82f,
                    Color.black,
                    0.72f),
                boot = Material(
                    "Hero Boots",
                    new Color(0.025f, 0.035f, 0.04f),
                    0.3f),
            };
            return Build(root, "Ye Qingge Visual", palette, false);
        }

        public static StylizedCharacterAnimator BuildGuard(Transform root)
        {
            var palette = new CharacterPalette
            {
                cloth = Material(
                    "Guard Wine Cloth",
                    new Color(0.34f, 0.06f, 0.05f),
                    0.3f),
                clothAccent = Material(
                    "Guard Dark Armor",
                    new Color(0.13f, 0.18f, 0.21f),
                    0.68f,
                    new Color(0.004f, 0.008f, 0.01f),
                    0.42f),
                accent = Material(
                    "Guard Old Bronze",
                    new Color(0.48f, 0.28f, 0.075f),
                    0.68f,
                    Color.black,
                    0.65f),
                skin = Material(
                    "Guard Weathered Skin",
                    new Color(0.66f, 0.44f, 0.34f),
                    0.36f),
                hair = Material(
                    "Guard Dark Hair",
                    new Color(0.012f, 0.015f, 0.019f),
                    0.48f),
                eye = Material(
                    "Guard Eyes",
                    new Color(0.012f, 0.009f, 0.008f),
                    0.62f),
                metal = Material(
                    "Guard Spear Steel",
                    new Color(0.36f, 0.4f, 0.42f),
                    0.86f,
                    Color.black,
                    0.76f),
                boot = Material(
                    "Guard Boots",
                    new Color(0.04f, 0.025f, 0.021f),
                    0.28f),
            };
            return Build(root, "Guard Visual", palette, true);
        }

        public static StylizedCharacterAnimator BuildQingqing(Transform root)
        {
            var palette = new CharacterPalette
            {
                cloth = Material(
                    "Qingqing Plum Cloth",
                    new Color(0.31f, 0.075f, 0.19f),
                    0.38f),
                clothAccent = Material(
                    "Qingqing Orchid Edge",
                    new Color(0.58f, 0.25f, 0.48f),
                    0.46f,
                    new Color(0.025f, 0.004f, 0.018f)),
                accent = Material(
                    "Qingqing Pale Sash",
                    new Color(0.78f, 0.57f, 0.67f),
                    0.3f),
                skin = Material(
                    "Qingqing Warm Skin",
                    new Color(0.86f, 0.66f, 0.55f),
                    0.42f),
                hair = Material(
                    "Qingqing Ink Hair",
                    new Color(0.012f, 0.009f, 0.017f),
                    0.56f),
                eye = Material(
                    "Qingqing Eyes",
                    new Color(0.03f, 0.012f, 0.022f),
                    0.68f),
                metal = Material(
                    "Qingqing Hairpin Silver",
                    new Color(0.52f, 0.49f, 0.56f),
                    0.82f,
                    Color.black,
                    0.68f),
                boot = Material(
                    "Qingqing Boots",
                    new Color(0.055f, 0.025f, 0.04f),
                    0.3f),
            };
            return Build(root, "Ye Qingqing Visual", palette, false);
        }

        private static StylizedCharacterAnimator Build(
            Transform root,
            string visualName,
            CharacterPalette palette,
            bool guard)
        {
            var visual = new GameObject(visualName);
            visual.transform.SetParent(root, false);

            var shadow = Primitive(
                PrimitiveType.Cylinder,
                "Grounded Shadow",
                visual.transform,
                new Vector3(0f, 0.018f, 0f),
                new Vector3(0.48f, 0.012f, 0.32f),
                Quaternion.identity,
                Material(
                    "Character Ground Shadow",
                    new Color(0.006f, 0.01f, 0.014f),
                    0.12f));
            shadow.GetComponent<Renderer>().shadowCastingMode =
                ShadowCastingMode.Off;

            var leftLeg = Pivot(visual.transform, "Left Leg Pivot",
                new Vector3(-0.19f, 0.86f, 0f));
            var rightLeg = Pivot(visual.transform, "Right Leg Pivot",
                new Vector3(0.19f, 0.86f, 0f));
            BuildLeg(leftLeg, "Left", palette, guard);
            BuildLeg(rightLeg, "Right", palette, guard);

            CreateTaperedBox(
                guard ? "Guard Under Tunic" : "Hero Inner Skirt",
                visual.transform,
                new Vector3(0f, 0.7f, 0f),
                guard ? 0.49f : 0.42f,
                guard ? 0.57f : 0.5f,
                guard ? 0.38f : 0.34f,
                guard ? 0.45f : 0.4f,
                guard ? 0.72f : 0.68f,
                guard ? palette.cloth : palette.boot);
            if (guard)
            {
                BuildGuardSkirtArmor(visual.transform, palette);
            }
            else
            {
                BuildHeroOverskirt(visual.transform, palette);
            }

            var spine = Pivot(visual.transform, "Spine Pivot",
                new Vector3(0f, 1.03f, 0f));
            CreateTaperedBox(
                guard ? "Armored Torso" : "Tailored Torso",
                spine,
                new Vector3(0f, 0.37f, 0f),
                guard ? 0.53f : 0.45f,
                guard ? 0.62f : 0.54f,
                0.34f,
                0.39f,
                0.72f,
                guard ? palette.clothAccent : palette.cloth);
            CreateTaperedBox(
                guard ? "Lamellar Chest Plate" : "Crossed Collar",
                spine,
                new Vector3(0f, guard ? 0.4f : 0.54f, 0.37f),
                guard ? 0.48f : 0.34f,
                guard ? 0.55f : 0.45f,
                0.035f,
                0.035f,
                guard ? 0.52f : 0.12f,
                guard ? palette.clothAccent : palette.clothAccent);
            Primitive(
                PrimitiveType.Cube,
                "Waist Sash",
                spine,
                new Vector3(0f, 0.04f, 0f),
                new Vector3(0.98f, 0.13f, 0.73f),
                Quaternion.identity,
                palette.accent);

            if (guard)
            {
                BuildArmorDetails(spine, palette);
            }
            else
            {
                BuildHeroDetails(spine, palette);
            }

            var leftArm = Pivot(
                spine,
                "Left Arm Pivot",
                new Vector3(-0.52f, 0.62f, 0f));
            var rightArm = Pivot(
                spine,
                "Right Arm Pivot",
                new Vector3(0.52f, 0.62f, 0f));
            BuildArm(leftArm, "Left", palette, guard);
            BuildArm(rightArm, "Right", palette, guard);

            var head = Pivot(
                visual.transform,
                "Head Pivot",
                new Vector3(0f, 2.02f, 0f));
            BuildHead(head, palette, guard);

            if (guard)
            {
                BuildSpear(visual.transform, palette);
            }
            else
            {
                BuildHeroHair(head, palette);
            }

            var focusMarker = BuildFocusMarker(visual.transform, palette.accent);
            var animator = root.GetComponent<StylizedCharacterAnimator>()
                ?? root.gameObject.AddComponent<StylizedCharacterAnimator>();
            animator.Configure(
                visual.transform,
                spine,
                head,
                leftArm,
                rightArm,
                leftLeg,
                rightLeg,
                focusMarker);
            return animator;
        }

        private static void BuildLeg(
            Transform pivot,
            string side,
            CharacterPalette palette,
            bool guard)
        {
            CreateTaperedBox(
                $"{side} Trouser",
                pivot,
                new Vector3(0f, -0.29f, 0f),
                0.16f,
                0.19f,
                0.16f,
                0.18f,
                0.58f,
                palette.boot);
            CreateTaperedBox(
                $"{side} Boot",
                pivot,
                new Vector3(0f, -0.65f, 0.045f),
                0.16f,
                0.18f,
                0.18f,
                0.28f,
                0.28f,
                palette.boot);
            Primitive(
                PrimitiveType.Cube,
                $"{side} Boot Cuff",
                pivot,
                new Vector3(0f, -0.52f, 0.015f),
                new Vector3(0.4f, 0.085f, 0.39f),
                Quaternion.identity,
                guard ? palette.accent : palette.metal);
            CreateTaperedBox(
                $"{side} Boot Toe",
                pivot,
                new Vector3(0f, -0.77f, 0.15f),
                0.15f,
                0.19f,
                0.24f,
                0.3f,
                0.12f,
                palette.boot);
        }

        private static void BuildHeroOverskirt(
            Transform visual,
            CharacterPalette palette)
        {
            for (var side = -1; side <= 1; side += 2)
            {
                CreateTaperedBox(
                    side < 0
                        ? "Hero Front Skirt Left"
                        : "Hero Front Skirt Right",
                    visual,
                    new Vector3(side * 0.23f, 0.7f, 0.4f),
                    0.19f,
                    0.27f,
                    0.035f,
                    0.045f,
                    0.82f,
                    palette.cloth);
                CreateTaperedBox(
                    side < 0
                        ? "Hero Back Skirt Left"
                        : "Hero Back Skirt Right",
                    visual,
                    new Vector3(side * 0.23f, 0.72f, -0.4f),
                    0.19f,
                    0.28f,
                    0.035f,
                    0.045f,
                    0.78f,
                    palette.cloth);
                CreateTaperedBox(
                    side < 0
                        ? "Hero Side Skirt Left"
                        : "Hero Side Skirt Right",
                    visual,
                    new Vector3(side * 0.52f, 0.74f, 0f),
                    0.12f,
                    0.18f,
                    0.28f,
                    0.36f,
                    0.74f,
                    palette.clothAccent);
                Primitive(
                    PrimitiveType.Cube,
                    side < 0
                        ? "Hero Front Hem Left"
                        : "Hero Front Hem Right",
                    visual,
                    new Vector3(side * 0.23f, 0.3f, 0.45f),
                    new Vector3(0.48f, 0.055f, 0.035f),
                    Quaternion.identity,
                    palette.clothAccent);
            }
            CreateTaperedBox(
                "Crimson Sash Tail",
                visual,
                new Vector3(0.05f, 0.66f, 0.465f),
                0.07f,
                0.11f,
                0.025f,
                0.03f,
                0.68f,
                palette.accent);
        }

        private static void BuildGuardSkirtArmor(
            Transform visual,
            CharacterPalette palette)
        {
            for (var face = -1; face <= 1; face += 2)
            {
                for (var column = -2; column <= 2; column++)
                {
                    CreateTaperedBox(
                        $"Guard Skirt Plate {face} {column}",
                        visual,
                        new Vector3(
                            column * 0.19f,
                            0.68f,
                            face * 0.43f),
                        0.075f,
                        0.095f,
                        0.035f,
                        0.045f,
                        0.62f,
                        palette.clothAccent);
                    if (face > 0)
                    {
                        Primitive(
                            PrimitiveType.Sphere,
                            $"Guard Skirt Rivet {column}",
                            visual,
                            new Vector3(
                                column * 0.19f,
                                0.82f,
                                0.472f),
                            new Vector3(0.045f, 0.045f, 0.025f),
                            Quaternion.identity,
                            palette.accent);
                    }
                }
            }
            for (var side = -1; side <= 1; side += 2)
            {
                for (var row = -1; row <= 1; row += 2)
                {
                    CreateTaperedBox(
                        $"Guard Side Plate {side} {row}",
                        visual,
                        new Vector3(
                            side * 0.54f,
                            0.68f,
                            row * 0.21f),
                        0.09f,
                        0.11f,
                        0.15f,
                        0.2f,
                        0.58f,
                        palette.clothAccent);
                }
            }
        }

        private static void BuildArm(
            Transform pivot,
            string side,
            CharacterPalette palette,
            bool guard)
        {
            var direction = side == "Left" ? 1f : -1f;
            pivot.localRotation = Quaternion.Euler(
                0f,
                0f,
                direction * (guard ? 7f : 11f));
            CreateTaperedBox(
                $"{side} Outer Sleeve",
                pivot,
                new Vector3(0f, -0.17f, 0f),
                guard ? 0.22f : 0.19f,
                guard ? 0.27f : 0.26f,
                0.19f,
                0.23f,
                0.34f,
                palette.cloth);
            CreateTaperedBox(
                $"{side} Inner Sleeve",
                pivot,
                new Vector3(0f, -0.42f, 0f),
                0.14f,
                0.18f,
                0.14f,
                0.17f,
                0.3f,
                guard ? palette.cloth : palette.boot);
            CreateTaperedBox(
                $"{side} Bracer",
                pivot,
                new Vector3(0f, -0.54f, 0.005f),
                0.145f,
                0.18f,
                0.145f,
                0.18f,
                0.24f,
                guard ? palette.clothAccent : palette.metal);
            Primitive(
                PrimitiveType.Cube,
                $"{side} Bracer Band",
                pivot,
                new Vector3(0f, -0.48f, 0.16f),
                new Vector3(0.27f, 0.055f, 0.035f),
                Quaternion.identity,
                palette.accent);
            Primitive(
                PrimitiveType.Sphere,
                $"{side} Hand",
                pivot,
                new Vector3(0f, -0.7f, 0.02f),
                new Vector3(0.19f, 0.21f, 0.18f),
                Quaternion.identity,
                palette.skin);
        }

        private static void BuildHead(
            Transform head,
            CharacterPalette palette,
            bool guard)
        {
            Primitive(
                PrimitiveType.Sphere,
                "Face",
                head,
                Vector3.zero,
                new Vector3(0.66f, 0.78f, 0.66f),
                Quaternion.identity,
                palette.skin);
            Primitive(
                PrimitiveType.Sphere,
                "Hair Cap",
                head,
                new Vector3(0f, 0.19f, -0.025f),
                new Vector3(0.72f, 0.48f, 0.68f),
                Quaternion.identity,
                palette.hair);

            for (var side = -1; side <= 1; side += 2)
            {
                Primitive(
                    PrimitiveType.Sphere,
                    side < 0 ? "Left Eye" : "Right Eye",
                    head,
                    new Vector3(side * 0.13f, 0.04f, 0.315f),
                    new Vector3(0.075f, 0.052f, 0.036f),
                    Quaternion.identity,
                    palette.eye);
                Primitive(
                    PrimitiveType.Cube,
                    side < 0 ? "Left Brow" : "Right Brow",
                    head,
                    new Vector3(side * 0.13f, 0.135f, 0.324f),
                    new Vector3(0.15f, 0.026f, 0.025f),
                    Quaternion.Euler(0f, 0f, side * (guard ? 8f : 3f)),
                    palette.hair);
            }

            CreateTaperedBox(
                "Nose",
                head,
                new Vector3(0f, -0.025f, 0.344f),
                0.035f,
                0.045f,
                0.035f,
                0.045f,
                0.13f,
                palette.skin);
            Primitive(
                PrimitiveType.Cube,
                "Mouth",
                head,
                new Vector3(0f, -0.17f, 0.325f),
                new Vector3(0.16f, 0.026f, 0.025f),
                Quaternion.identity,
                guard ? palette.hair : palette.accent);

            if (guard)
            {
                Primitive(
                    PrimitiveType.Cylinder,
                    "Guard Hat Brim",
                    head,
                    new Vector3(0f, 0.39f, 0f),
                    new Vector3(0.51f, 0.055f, 0.51f),
                    Quaternion.identity,
                    palette.hair);
                Primitive(
                    PrimitiveType.Cylinder,
                    "Guard Bronze Helmet Band",
                    head,
                    new Vector3(0f, 0.4f, 0f),
                    new Vector3(0.53f, 0.035f, 0.53f),
                    Quaternion.identity,
                    palette.accent);
                CreateTaperedBox(
                    "Guard Hat Crown",
                    head,
                    new Vector3(0f, 0.53f, 0f),
                    0.24f,
                    0.34f,
                    0.24f,
                    0.34f,
                    0.26f,
                    palette.hair);
                Primitive(
                    PrimitiveType.Sphere,
                    "Guard Helmet Knot",
                    head,
                    new Vector3(0f, 0.7f, -0.015f),
                    new Vector3(0.22f, 0.18f, 0.22f),
                    Quaternion.identity,
                    palette.hair);
                for (var side = -1; side <= 1; side += 2)
                {
                    CreateTaperedBox(
                        side < 0
                            ? "Guard Helmet Flap Left"
                            : "Guard Helmet Flap Right",
                        head,
                        new Vector3(side * 0.29f, 0.16f, -0.11f),
                        0.075f,
                        0.12f,
                        0.055f,
                        0.075f,
                        0.42f,
                        palette.hair);
                }
                Primitive(
                    PrimitiveType.Cube,
                    "Short Beard",
                    head,
                    new Vector3(0f, -0.29f, 0.28f),
                    new Vector3(0.28f, 0.17f, 0.08f),
                    Quaternion.Euler(10f, 0f, 0f),
                    palette.hair);
            }
        }

        private static void BuildHeroHair(
            Transform head,
            CharacterPalette palette)
        {
            Primitive(
                PrimitiveType.Sphere,
                "High Hair Knot",
                head,
                new Vector3(0f, 0.53f, -0.02f),
                new Vector3(0.28f, 0.34f, 0.28f),
                Quaternion.identity,
                palette.hair);
            Primitive(
                PrimitiveType.Cylinder,
                "Jade Hair Pin",
                head,
                new Vector3(0f, 0.58f, -0.02f),
                new Vector3(0.035f, 0.34f, 0.035f),
                Quaternion.Euler(0f, 0f, 90f),
                palette.clothAccent);
            var ponytail = Pivot(
                head,
                "High Ponytail Pivot",
                new Vector3(0f, 0.42f, -0.2f));
            ponytail.gameObject.AddComponent<AmbientSway>().Configure(
                Vector3.right,
                4f,
                0.72f,
                2.2f);
            CreateTaperedBox(
                "Ponytail Upper",
                ponytail,
                new Vector3(0f, -0.19f, -0.03f),
                0.15f,
                0.2f,
                0.06f,
                0.1f,
                0.42f,
                palette.hair);
            CreateTaperedBox(
                "Ponytail Middle",
                ponytail,
                new Vector3(0f, -0.53f, -0.09f),
                0.11f,
                0.16f,
                0.045f,
                0.075f,
                0.34f,
                palette.hair);
            CreateTaperedBox(
                "Ponytail Tip",
                ponytail,
                new Vector3(0f, -0.8f, -0.14f),
                0.035f,
                0.115f,
                0.025f,
                0.05f,
                0.26f,
                palette.hair);
            for (var side = -1; side <= 1; side += 2)
            {
                CreateTaperedBox(
                    side < 0 ? "Left Face Lock" : "Right Face Lock",
                    head,
                    new Vector3(side * 0.27f, -0.02f, 0.2f),
                    0.045f,
                    0.075f,
                    0.035f,
                    0.05f,
                    0.47f,
                    palette.hair);
            }
        }

        private static void BuildHeroDetails(
            Transform spine,
            CharacterPalette palette)
        {
            Primitive(
                PrimitiveType.Cube,
                "Diagonal Collar Left",
                spine,
                new Vector3(-0.13f, 0.51f, 0.385f),
                new Vector3(0.36f, 0.07f, 0.035f),
                Quaternion.Euler(0f, 0f, -34f),
                palette.clothAccent);
            Primitive(
                PrimitiveType.Cube,
                "Diagonal Collar Right",
                spine,
                new Vector3(0.13f, 0.51f, 0.39f),
                new Vector3(0.36f, 0.07f, 0.035f),
                Quaternion.Euler(0f, 0f, 34f),
                palette.clothAccent);
            Primitive(
                PrimitiveType.Cylinder,
                "Sash Ornament",
                spine,
                new Vector3(0f, 0.035f, 0.41f),
                new Vector3(0.1f, 0.035f, 0.1f),
                Quaternion.Euler(90f, 0f, 0f),
                palette.metal);
        }

        private static void BuildArmorDetails(
            Transform spine,
            CharacterPalette palette)
        {
            for (var face = -1; face <= 1; face += 2)
            {
                for (var row = 0; row < 3; row++)
                {
                    for (var column = -2; column <= 2; column++)
                    {
                        Primitive(
                            PrimitiveType.Cube,
                            $"Armor Plate {face} {row} {column}",
                            spine,
                            new Vector3(
                                column * 0.19f,
                                0.59f - row * 0.18f,
                                face * 0.405f),
                            new Vector3(0.16f, 0.14f, 0.045f),
                            Quaternion.identity,
                            palette.clothAccent);
                        if (face > 0)
                        {
                            Primitive(
                                PrimitiveType.Sphere,
                                $"Armor Rivet {row} {column}",
                                spine,
                                new Vector3(
                                    column * 0.19f,
                                    0.59f - row * 0.18f,
                                    0.447f),
                                new Vector3(
                                    0.035f,
                                    0.035f,
                                    0.022f),
                                Quaternion.identity,
                                palette.accent);
                        }
                    }
                }
            }
            Primitive(
                PrimitiveType.Sphere,
                "Left Pauldron",
                spine,
                new Vector3(-0.54f, 0.61f, 0f),
                new Vector3(0.32f, 0.19f, 0.42f),
                Quaternion.Euler(0f, 0f, -8f),
                palette.accent);
            Primitive(
                PrimitiveType.Sphere,
                "Right Pauldron",
                spine,
                new Vector3(0.54f, 0.61f, 0f),
                new Vector3(0.32f, 0.19f, 0.42f),
                Quaternion.Euler(0f, 0f, 8f),
                palette.accent);
            Primitive(
                PrimitiveType.Cube,
                "Guard Bronze Belt Buckle",
                spine,
                new Vector3(0f, 0.035f, 0.42f),
                new Vector3(0.32f, 0.2f, 0.075f),
                Quaternion.identity,
                palette.accent);
        }

        private static void BuildSpear(
            Transform visual,
            CharacterPalette palette)
        {
            var spear = Pivot(
                visual,
                "Guard Spear",
                new Vector3(0.72f, 1.22f, 0.02f));
            spear.localRotation = Quaternion.Euler(0f, 0f, -4f);
            Primitive(
                PrimitiveType.Cylinder,
                "Spear Shaft",
                spear,
                Vector3.zero,
                new Vector3(0.045f, 1.38f, 0.045f),
                Quaternion.identity,
                palette.hair);
            CreateTaperedBox(
                "Spear Head",
                spear,
                new Vector3(0f, 1.55f, 0f),
                0.025f,
                0.14f,
                0.025f,
                0.09f,
                0.34f,
                palette.metal);
            Primitive(
                PrimitiveType.Cube,
                "Spear Tassel",
                spear,
                new Vector3(0f, 1.28f, 0f),
                new Vector3(0.16f, 0.22f, 0.08f),
                Quaternion.Euler(0f, 0f, 8f),
                palette.cloth);
        }

        private static Transform BuildFocusMarker(
            Transform visual,
            Material material)
        {
            var marker = Pivot(
                visual,
                "Interaction Focus",
                new Vector3(0f, 2.95f, 0f));
            for (var index = 0; index < 4; index++)
            {
                Primitive(
                    PrimitiveType.Cube,
                    $"Focus Corner {index}",
                    marker,
                    Quaternion.Euler(0f, index * 90f, 0f)
                        * new Vector3(0f, 0f, 0.22f),
                    new Vector3(0.07f, 0.07f, 0.18f),
                    Quaternion.Euler(0f, index * 90f, 45f),
                    material);
            }
            marker.gameObject.SetActive(false);
            return marker;
        }

        private static Transform Pivot(
            Transform parent,
            string name,
            Vector3 position)
        {
            var pivot = new GameObject(name).transform;
            pivot.SetParent(parent, false);
            pivot.localPosition = position;
            return pivot;
        }

        private static void CreateTaperedBox(
            string name,
            Transform parent,
            Vector3 position,
            float topWidth,
            float bottomWidth,
            float topDepth,
            float bottomDepth,
            float height,
            Material material)
        {
            var target = new GameObject(name);
            target.transform.SetParent(parent, false);
            target.transform.localPosition = position;
            var filter = target.AddComponent<MeshFilter>();
            filter.sharedMesh = TaperedBoxMesh(
                name,
                topWidth,
                bottomWidth,
                topDepth,
                bottomDepth,
                height);
            var renderer = target.AddComponent<MeshRenderer>();
            renderer.sharedMaterial = material;
            renderer.shadowCastingMode = ShadowCastingMode.On;
            renderer.receiveShadows = true;
        }

        private static Mesh TaperedBoxMesh(
            string name,
            float topWidth,
            float bottomWidth,
            float topDepth,
            float bottomDepth,
            float height)
        {
            var y = height * 0.5f;
            var vertices = new[]
            {
                new Vector3(-bottomWidth, -y, -bottomDepth),
                new Vector3(bottomWidth, -y, -bottomDepth),
                new Vector3(bottomWidth, -y, bottomDepth),
                new Vector3(-bottomWidth, -y, bottomDepth),
                new Vector3(-topWidth, y, -topDepth),
                new Vector3(topWidth, y, -topDepth),
                new Vector3(topWidth, y, topDepth),
                new Vector3(-topWidth, y, topDepth),
            };
            var triangles = new[]
            {
                0, 4, 5, 0, 5, 1,
                1, 5, 6, 1, 6, 2,
                2, 6, 7, 2, 7, 3,
                3, 7, 4, 3, 4, 0,
                4, 7, 6, 4, 6, 5,
                3, 0, 1, 3, 1, 2,
            };
            var mesh = new Mesh
            {
                name = $"{name} Mesh",
                hideFlags = HideFlags.DontSave,
                vertices = vertices,
                triangles = triangles,
            };
            mesh.RecalculateNormals();
            mesh.RecalculateBounds();
            return mesh;
        }

        private static GameObject Primitive(
            PrimitiveType type,
            string name,
            Transform parent,
            Vector3 position,
            Vector3 scale,
            Quaternion rotation,
            Material material)
        {
            var target = GameObject.CreatePrimitive(type);
            target.name = name;
            target.transform.SetParent(parent, false);
            target.transform.localPosition = position;
            target.transform.localScale = scale;
            target.transform.localRotation = rotation;
            var renderer = target.GetComponent<Renderer>();
            renderer.sharedMaterial = material;
            renderer.shadowCastingMode = ShadowCastingMode.On;
            renderer.receiveShadows = true;
            var collider = target.GetComponent<Collider>();
            if (collider != null)
            {
                Object.Destroy(collider);
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
            if (material.HasProperty("_BaseColor"))
            {
                material.SetColor("_BaseColor", color);
            }
            if (material.HasProperty("_Color"))
            {
                material.SetColor("_Color", color);
            }
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

        private sealed class CharacterPalette
        {
            public Material cloth;
            public Material clothAccent;
            public Material accent;
            public Material skin;
            public Material hair;
            public Material eye;
            public Material metal;
            public Material boot;
        }
    }
}
