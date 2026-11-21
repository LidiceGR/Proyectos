-- Demonstration table for cross-procedure registration checks.
-- This example is synthetic, not a statement of actual business policy.
CREATE TABLE dbo.OperationChecks (
    CheckId INT NOT NULL PRIMARY KEY,
    OperationId INT NOT NULL,
    Status VARCHAR(20) NOT NULL
);
GO
