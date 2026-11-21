-- Example of a cross-procedure rule: block registration when a check is pending.
-- The called RegisterOperation still enforces its own OperationType/Amount guard.
CREATE PROCEDURE dbo.RegisterOperationAfterCheck
    @OperationId INT,
    @OperationType INT,
    @Amount DECIMAL(18, 2)
AS
BEGIN
    DECLARE @HasPending BIT;
    EXEC dbo.HasPendingOperationCheck @OperationId, @HasPending OUTPUT;
    IF @HasPending = 0
    BEGIN
        EXEC dbo.RegisterOperation @OperationId, @OperationType, @Amount;
    END;
END;
GO
