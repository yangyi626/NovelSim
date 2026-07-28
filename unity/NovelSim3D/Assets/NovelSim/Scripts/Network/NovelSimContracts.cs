using System;

namespace NovelSim.Network
{
    [Serializable]
    public class ApiResponse
    {
        public string status;
        public string error;
    }

    [Serializable]
    public sealed class WorldMetaDto
    {
        public string scenario;
        public string novel;
        public string anchor;
    }

    [Serializable]
    public sealed class WorldStateDto
    {
        public string timeline_id;
        public int version;
        public string world_time;
        public string current_scene_id;
    }

    [Serializable]
    public sealed class SaveDto
    {
        public string session_id;
        public string name;
        public string world_package_id;
    }

    [Serializable]
    public sealed class SessionResponse : ApiResponse
    {
        public string session_id;
        public string default_actor;
        public WorldMetaDto world_meta;
        public WorldStateDto state;
        public SaveDto save;
        public bool resumed;
    }

    [Serializable]
    public sealed class TurnActionDto
    {
        public string type;
        public string actor;
        public string[] targets;
        public string goal;
        public string visibility;
    }

    [Serializable]
    public sealed class DialogueDto
    {
        public string speaker_id;
        public string line;
        public string tone;
        public string to_id;
    }

    [Serializable]
    public sealed class NarrativeDto
    {
        public string narration;
        public DialogueDto[] dialogues;
        public string[] system_hints;
    }

    [Serializable]
    public sealed class TurnResponse : ApiResponse
    {
        public string rule_reason;
        public string memory_warning;
        public TurnActionDto action;
        public NarrativeDto narrative;
        public WorldStateDto state;
    }

    [Serializable]
    internal sealed class StartRequest
    {
        public string package_id;
        public string save_name;
    }

    [Serializable]
    internal sealed class TurnRequest
    {
        public string session_id;
        public string text;
        public bool use_npc_agents;
    }
}
