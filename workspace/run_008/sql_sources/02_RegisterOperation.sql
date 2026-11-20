-- Business meaning of OperationType = 2 requires supporting catalog evidence.
CREATE PROCEDURE dbo.RegisterOperation
    @OperationId INT,
    @OperationType INT,
    @Amount DECIMAL(18, 2)
AS
BEGIN
    IF @OperationType = 2 AND @Amount > 0
    BEGIN
        INSERT INTO dbo.Operations (OperationId, OperationType, Amount, Status)
        VALUES (@OperationId, @OperationType, @Amount, 'PENDING');
    END
END;
GO
