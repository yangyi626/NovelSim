using System.Collections;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;
using NovelSim.Core;
using NovelSim.Interaction;
using NovelSim.World;

namespace NovelSim.Tests
{
    public sealed class VerticalSliceSmokeTests
    {
        [UnityTest]
        public IEnumerator BootstrapCreatesPlayableServerAuthoritativeWorld()
        {
            var bootstrap = Object.FindFirstObjectByType<
                VerticalSliceBootstrap>();
            if (bootstrap == null)
            {
                bootstrap = new GameObject("NovelSim PlayMode Test")
                    .AddComponent<VerticalSliceBootstrap>();
            }

            yield return null;

            var player = GameObject.Find("Player");
            var npc = GameObject.Find("Lane Guard NPC");
            Assert.IsNotNull(player);
            Assert.IsNotNull(npc);
            Assert.IsNotNull(player.GetComponent<CharacterController>());
            Assert.IsNotNull(npc.GetComponent<InteractionTarget>());
            Assert.GreaterOrEqual(
                player.GetComponentsInChildren<Renderer>().Length,
                8);
            Assert.GreaterOrEqual(
                npc.GetComponentsInChildren<Renderer>().Length,
                8);
            Assert.IsNotNull(GameObject.Find("Huarong Lane Art"));
            Assert.IsTrue(RenderSettings.fog);
            Assert.AreEqual(52f, Camera.main.fieldOfView, 0.1f);
            Assert.IsNotNull(Object.FindFirstObjectByType<
                WorldSessionManager>());
        }
    }
}
