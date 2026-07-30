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
        public string rejection_code;
        public string rejection_message;
        public string memory_warning;
        public TurnActionDto action;
        public NarrativeDto narrative;
        public WorldStateDto state;
    }

    [Serializable]
    public sealed class SecretLetterRunResponse : ApiResponse
    {
        public string session_id;
        public string default_actor;
        public WorldMetaDto world_meta;
        public SaveDto save;
        public bool resumed;
        public string world_package_id;
        public string route;
        public string mode;
        public string ending;
        public bool objective_satisfied;
        public WorldStateDto state;
        public int memory_record_count;
        public string memory_warning;
        public long presentation_cursor;
    }

    [Serializable]
    public sealed class PresentationCommandDto
    {
        public long sequence;
        public string command_id;
        public string event_id;
        public int world_version;
        public string command_type;
        public string actor_id;
        public string target_id;
        public string entity_id;
        public string location_id;
        public string fact_id;
        public string alliance_id;
        public string text;
        public string tone;
        public string[] member_ids;
    }

    [Serializable]
    public sealed class PresentationEventsResponse : ApiResponse
    {
        public string session_id;
        public int state_version;
        public long after_sequence;
        public long next_sequence;
        public long latest_sequence;
        public bool has_more;
        public PresentationCommandDto[] commands;
    }

    [Serializable]
    public sealed class CharacterPresentationStateDto
    {
        public string character_id;
        public string display_name;
        public string location_id;
        public bool is_alive;
        public string[] inventory;
    }

    [Serializable]
    public sealed class ItemPresentationStateDto
    {
        public string item_id;
        public string display_name;
        public string owner_id;
        public string location_id;
        public int quantity;
        public bool accessible;
        public bool destroyed;
    }

    [Serializable]
    public sealed class AlliancePresentationStateDto
    {
        public string alliance_id;
        public string[] member_ids;
        public string goal_key;
        public string status;
    }

    [Serializable]
    public sealed class PresentationSnapshotDto
    {
        public string timeline_id;
        public int state_version;
        public string current_scene_id;
        public long last_sequence;
        public CharacterPresentationStateDto[] characters;
        public ItemPresentationStateDto[] items;
        public AlliancePresentationStateDto[] alliances;
    }

    [Serializable]
    public sealed class PresentationSnapshotResponse : ApiResponse
    {
        public string session_id;
        public PresentationSnapshotDto snapshot;
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

    [Serializable]
    internal sealed class SecretLetterRunRequest
    {
        public string mode;
        public string route;
        public string save_name;
    }
}
