-- Independent constraint: reject inserted or updated rows with non-positive amounts.
CREATE TRIGGER dbo.trg_Operations_ValidateAmount
ON dbo.Operations
AFTER INSERT, UPDATE
AS
BEGIN
    IF EXISTS (SELECT 1 FROM inserted WHERE Amount <= 0)
    BEGIN
        THROW 50001, 'Amount must be greater than zero', 1;
    END
END;
GO
