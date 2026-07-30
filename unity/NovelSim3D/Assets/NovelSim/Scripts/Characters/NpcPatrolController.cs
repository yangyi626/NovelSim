using UnityEngine;
using UnityEngine.AI;

namespace NovelSim.Characters
{
    [RequireComponent(typeof(NavMeshAgent))]
    public sealed class NpcPatrolController : MonoBehaviour
    {
        [SerializeField]
        private float attentionDistance = 3.2f;

        [SerializeField]
        private float waypointPause = 1.2f;

        private NavMeshAgent agent;
        private Transform focusTarget;
        private StylizedCharacterAnimator presentation;
        private Vector3[] waypoints;
        private int waypointIndex;
        private float pauseRemaining;
        private bool configured;
        private bool hasCommandDestination;
        private Vector3 commandDestination;

        public bool IsOnNavMesh =>
            agent != null && agent.enabled && agent.isOnNavMesh;

        public bool IsPatrolling =>
            IsOnNavMesh && !agent.isStopped && agent.hasPath;

        public bool HasCommandDestination => hasCommandDestination;

        public bool NavigateTo(Vector3 destination)
        {
            if (!configured || (!IsOnNavMesh && !TryPlaceOnNavMesh()))
            {
                return false;
            }
            if (!NavMesh.SamplePosition(
                destination,
                out var hit,
                2.5f,
                NavMesh.AllAreas))
            {
                return false;
            }
            commandDestination = hit.position;
            hasCommandDestination = true;
            pauseRemaining = 0f;
            agent.isStopped = false;
            return agent.SetDestination(commandDestination);
        }

        public void Face(Transform target)
        {
            if (target == null)
            {
                return;
            }
            FaceTarget(target.position);
        }

        public void Configure(
            Transform player,
            params Vector3[] patrolWaypoints)
        {
            focusTarget = player;
            waypoints = patrolWaypoints ?? new Vector3[0];
            agent = GetComponent<NavMeshAgent>();
            presentation = GetComponent<StylizedCharacterAnimator>();
            agent.speed = 1.25f;
            agent.angularSpeed = 280f;
            agent.acceleration = 5f;
            agent.stoppingDistance = 0.12f;
            agent.autoBraking = true;
            agent.radius = 0.32f;
            agent.height = 2.1f;
            agent.obstacleAvoidanceType =
                ObstacleAvoidanceType.MedQualityObstacleAvoidance;
            configured = true;
            TryPlaceOnNavMesh();
        }

        private void Update()
        {
            if (!configured)
            {
                return;
            }
            if (!IsOnNavMesh && !TryPlaceOnNavMesh())
            {
                presentation?.SetLocomotion(0f);
                return;
            }

            if (hasCommandDestination)
            {
                if (!agent.hasPath && !agent.pathPending)
                {
                    agent.isStopped = false;
                    agent.SetDestination(commandDestination);
                }
                if (
                    !agent.pathPending
                    && agent.remainingDistance
                        <= agent.stoppingDistance + 0.08f)
                {
                    hasCommandDestination = false;
                    agent.isStopped = true;
                    agent.ResetPath();
                    presentation?.SetLocomotion(0f);
                    return;
                }
                presentation?.SetLocomotion(
                    agent.speed <= 0f
                        ? 0f
                        : agent.velocity.magnitude / agent.speed);
                return;
            }

            if (ShouldAttendToPlayer())
            {
                agent.isStopped = true;
                agent.ResetPath();
                FaceTarget(focusTarget.position);
                presentation?.SetLocomotion(0f);
                return;
            }

            if (waypoints == null || waypoints.Length == 0)
            {
                agent.isStopped = true;
                presentation?.SetLocomotion(0f);
                return;
            }

            if (pauseRemaining > 0f)
            {
                pauseRemaining -= Time.deltaTime;
                agent.isStopped = true;
                presentation?.SetLocomotion(0f);
                return;
            }

            if (!agent.hasPath)
            {
                SetNextDestination();
            }
            else if (
                !agent.pathPending
                && agent.remainingDistance
                    <= agent.stoppingDistance + 0.08f)
            {
                agent.ResetPath();
                pauseRemaining = waypointPause;
                waypointIndex = (waypointIndex + 1) % waypoints.Length;
            }
            presentation?.SetLocomotion(
                agent.speed <= 0f
                    ? 0f
                    : agent.velocity.magnitude / agent.speed);
        }

        private bool TryPlaceOnNavMesh()
        {
            if (agent == null)
            {
                agent = GetComponent<NavMeshAgent>();
            }
            if (agent.isOnNavMesh)
            {
                return true;
            }
            if (!NavMesh.SamplePosition(
                transform.position,
                out var hit,
                2.5f,
                NavMesh.AllAreas))
            {
                return false;
            }
            return agent.Warp(hit.position);
        }

        private bool ShouldAttendToPlayer()
        {
            if (focusTarget == null)
            {
                return false;
            }
            var delta = focusTarget.position - transform.position;
            delta.y = 0f;
            return delta.sqrMagnitude
                <= attentionDistance * attentionDistance;
        }

        private void FaceTarget(Vector3 position)
        {
            var direction = position - transform.position;
            direction.y = 0f;
            if (direction.sqrMagnitude < 0.001f)
            {
                return;
            }
            transform.rotation = Quaternion.Slerp(
                transform.rotation,
                Quaternion.LookRotation(direction.normalized, Vector3.up),
                Time.deltaTime * 6f);
        }

        private void SetNextDestination()
        {
            agent.isStopped = false;
            var target = waypoints[waypointIndex];
            if (NavMesh.SamplePosition(
                target,
                out var hit,
                2f,
                NavMesh.AllAreas))
            {
                agent.SetDestination(hit.position);
                return;
            }
            waypointIndex = (waypointIndex + 1) % waypoints.Length;
        }
    }
}
