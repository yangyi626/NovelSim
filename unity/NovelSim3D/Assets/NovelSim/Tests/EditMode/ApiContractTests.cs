using NUnit.Framework;
using UnityEngine;
using NovelSim.Network;

namespace NovelSim.Tests
{
    public sealed class ApiContractTests
    {
        [Test]
        public void ContractMajorVersionIsFrozen()
        {
            Assert.AreEqual("1.0.0", ApiContractV1.Version);
            Assert.AreEqual(1, ApiContractV1.MajorVersion);
            Assert.AreEqual("/api/start", ApiContractV1.StartSession);
            Assert.AreEqual("/api/turn", ApiContractV1.SubmitTurn);
        }

        [Test]
        public void TurnResponseParsesAuthoritativeStateAndNarrative()
        {
            const string json =
                "{\"status\":\"committed\",\"state\":{\"timeline_id\":\"t\","
                + "\"version\":2,\"world_time\":\"午后\","
                + "\"current_scene_id\":\"lane\"},\"narrative\":{"
                + "\"narration\":\"她走近守卫。\",\"dialogues\":[]}}";

            var response = JsonUtility.FromJson<TurnResponse>(json);

            Assert.AreEqual(2, response.state.version);
            Assert.AreEqual("lane", response.state.current_scene_id);
            Assert.AreEqual("她走近守卫。", response.narrative.narration);
        }
    }
}
