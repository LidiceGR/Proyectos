use asigna_etb
go
drop proc spsae_seg_usu_admin
go
create proc spsae_seg_usu_admin
            @clave_usuario  varchar(30)
as

declare @admin char(1)

select @admin = 'N' 

if exists ( select 1 from USUARIO_MODULO where clave_usuario = @clave_usuario and status = 'A') 
   select @admin = 'S'
   
select @admin admin_autorizado   

return 0 
go 
grant execute on spsae_seg_usu_admin to web_app
go