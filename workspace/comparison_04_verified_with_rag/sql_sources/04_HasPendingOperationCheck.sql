-- A helper procedure exposes a pending-check flag to callers.
CREATE PROCEDURE dbo.HasPendingOperationCheck
    @OperationId INT,
    @HasPending BIT OUTPUT
AS
BEGIN
    SET @HasPending = 0;
    IF EXISTS (
        SELECT 1
        FROM dbo.OperationChecks
        WHERE OperationId = @OperationId
          AND Status = 'PENDING'
    )
    BEGIN
        SET @HasPending = 1;
    END;
END;
GO
