"""Funcs to run Claude syncs with the database."""

import logging

from osaeru

logger = logging.getLogger(__name__)


def run_sync(conn) -> int:
    """Runs the sync process to process pending items in the sync queue.

    Args:
        conn (_type_): Database connection.

    Returns:
        int: The number of items processed from the sync queue.
    """
    logger.info("Sync starting")
    processed = 0
    flowchart = db.get_flowchart(conn)

    pending = db.pending_sync_items(conn)
    logger.info(f"Found {len(pending)} pending sync item(s)")

    if not pending:
        logger.warning(
            "No pending items - nothing to sync. "
            "If you expected items here, check sync_queue table directly."
        )

    for item in pending:
        logger.info(f"Processing item queue id={item['id']} kind={item['kind']}")
        payload = json.loads(item["payload"])

        try:
            if item["kind"] == "session_note":
                result = analyse_session_note(payload["notes"], flowchart)

                for skill in result.get("new_skills", []):
                    logger.info(f"Upserting skill: {skill}")
                    db.upsert_skill(
                        conn,
                        skill["name"],
                        skill.get("category"),
                        skill.get("confidence"),
                        session_id=payload.get("session_id"),
                    )

                for edge in result.get("new_edges", []):
                    logger.info(f"Adding edge: {edge}")
                    db.add_skill_edge(
                        conn,
                        edge["from"],
                        edge["to"],
                        edge["relation"],
                    )

                for rec in result.get("recommendations", []):
                    logger.info(f"Adding recommendations: {rec}")
                    related = (
                        db.upsert_skill(conn, rec["related_skill"])
                        if rec.get("related_skill")
                        else None
                    )
                    db.add_recommendation(conn, rec["text"], related)

                flowchart = db.get_flowchart(conn)

            elif item["kind"] == "chat_message":
                logger.debug(
                    f"Skipping chat_message item id={item['id']} (handled live, not via queue)"
                )

            db.mark_synced(conn, item["id"])
            processed += 1
            logger.info(f"Item id={item['id']} marked synced")

        except Exception:
            logger.error(
                "Failed processing item id=%s:\n%s", item["id"], traceback.format_exc()
            )
            raise

    logger.info(f"Sync finished, processed={processed}")

    return processed