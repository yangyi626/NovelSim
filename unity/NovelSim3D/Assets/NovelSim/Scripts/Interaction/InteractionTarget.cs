using UnityEngine;

namespace NovelSim.Interaction
{
    public sealed class InteractionTarget : MonoBehaviour
    {
        [SerializeField]
        private string displayName = "NPC";

        [SerializeField, TextArea]
        private string serverAction = "与面前的人交谈";

        public string DisplayName => displayName;
        public string ServerAction => serverAction;

        public void Configure(string name, string action)
        {
            displayName = name;
            serverAction = action;
        }
    }
}
