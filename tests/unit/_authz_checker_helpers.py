async def write_shared_helper_audit(writer) -> None:
    await writer.write_contract("POST", "/api/v1/test/helper-audit")
