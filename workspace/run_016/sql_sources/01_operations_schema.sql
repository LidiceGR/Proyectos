-- DEMO ONLY: simplified SQL Server schema, with fictional business data.
CREATE TABLE dbo.OperationTypes (
    OperationType INT NOT NULL PRIMARY KEY,
    Description NVARCHAR(100) NOT NULL
);
GO

CREATE TABLE dbo.OperationStatuses (
    Status VARCHAR(20) NOT NULL PRIMARY KEY,
    Description NVARCHAR(100) NOT NULL
);
GO

CREATE TABLE dbo.Operations (
    OperationId INT NOT NULL PRIMARY KEY,
    OperationType INT NOT NULL,
    Amount DECIMAL(18, 2) NOT NULL,
    Status VARCHAR(20) NOT NULL,
    CONSTRAINT FK_Operations_Type FOREIGN KEY (OperationType)
        REFERENCES dbo.OperationTypes(OperationType),
    CONSTRAINT FK_Operations_Status FOREIGN KEY (Status)
        REFERENCES dbo.OperationStatuses(Status)
);
GO
