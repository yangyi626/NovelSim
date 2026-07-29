using UnityEngine;
using NovelSim.Characters;

namespace NovelSim.Interaction
{
    public sealed class InteractionTarget : MonoBehaviour
    {
        [SerializeField]
        private string displayName = "NPC";

        [SerializeField, TextArea]
        private string serverAction = "与面前的人交谈";

        private StylizedCharacterAnimator presentation;

        public string DisplayName => displayName;
        public string ServerAction => serverAction;
        public bool IsFocused => presentation != null && presentation.IsFocused;

        public void Configure(string name, string action)
        {
            displayName = name;
            serverAction = action;
            presentation = GetComponent<StylizedCharacterAnimator>();
        }

        public void SetFocused(bool focused)
        {
            if (presentation == null)
            {
                presentation = GetComponent<StylizedCharacterAnimator>();
            }
            presentation?.SetFocused(focused);
        }

        public void NotifyInteractionSubmitted()
        {
            if (presentation == null)
            {
                presentation = GetComponent<StylizedCharacterAnimator>();
            }
            presentation?.PlayInteractionReaction();
        }
    }
}
