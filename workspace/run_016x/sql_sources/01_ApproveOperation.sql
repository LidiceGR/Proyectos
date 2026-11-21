-- Business rule candidate: only pending operations with a positive amount can be approved.
CREATE PROCEDURE dbo.ApproveOperation
    @OperationId INT
AS
BEGIN
    UPDATE dbo.Operations
       SET Status = 'APPROVED'
     WHERE OperationId = @OperationId
       AND Status = 'PENDING'
       AND Amount > 0;
END;
GO
