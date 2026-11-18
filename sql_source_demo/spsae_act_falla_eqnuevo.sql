use asigna_etb
go
drop proc spsae_act_falla_eqnuevo
go
create proc spsae_act_falla_eqnuevo
            @incitdente    int, 
			@num_inventario  varchar(20),
			@solucion        varchar(700),
			@clave_cambio    char(1) 
as
declare @status int

begin tran actEQUIPO_NUEVO

update EQUIPO_NUEVO 
   set status = 'N'
 where num_inventario = @num_inventario 
 
 
select @status = @@error
if ( @status <> 0 ) 
   begin
   rollback actEQUIPO_NUEVO
   return @status
   end


update EQUIPO_NUEVO_FALLA 
  set solucion = @solucion,
      status = 'E'       
 where  incidente = @incitdente
   and num_inventario = @num_inventario 
   
   
   
select @status = @@error
if ( @status <> 0 ) 
   begin
   rollback actEQUIPO_NUEVO
   return @status
   end
    	  
   


commit actEQUIPO_NUEVO



return 0 

go
grant execute on spsae_act_falla_eqnuevo to web_app
go
