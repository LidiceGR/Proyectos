


use asigna_etb
go
drop proc spsae_usu_acepta_equipo
go
create proc spsae_usu_acepta_equipo
            @num_inventario     varchar(20),
			@clave_usuario      varchar(30)
as

declare @status  int, @num_cambio int, @operacion int,@cita int,  @id  int, @fecha_recoleccion datetime,@inv_viejo varchar(20)

--if not exists ( select 1 from EQUIPO_ASIGNADO where num_inventario = @num_inventario and clave_usuario = @clave_usuario ) return 50026

if not exists ( select 1 from EQUIPO_ASIGNADO  where num_inventario = @num_inventario and edo_asignacion = 'V' ) return 50021 

create table #asociado ( operacion int  identity , num_inventario varchar(20) )

insert into #asociado 
     select num_inventario_hijo from ASOCIADO where num_inventario = @num_inventario



begin tran actEQUIPO_ASIGNADO

update EQUIPO_ASIGNADO 
   set edo_asignacion = 'A',  
       status = 'A'
 where num_inventario = @num_inventario
 
select @status = @@error
if ( @status <> 0 ) 
   begin
   rollback actEQUIPO_ASIGNADO
   return @status
   end   
   
select @num_cambio = isnull(max(num_cambio),0)+1 from EQUIPO_ESTADO where num_inventario = @num_inventario   

insert into EQUIPO_ESTADO 
            ( num_cambio, num_inventario, edo_asignacion, f_cambio, clave_cambio) 
	 values (@num_cambio,@num_inventario,'A',getdate(),@clave_usuario)
	 
select @status = @@error
if ( @status <> 0 ) 
   begin
   rollback actEQUIPO_ASIGNADO
   return @status
   end
   
update EQUIPO_NUEVO 
   set status = 'T' 
 where num_inventario = @num_inventario    
 
select @status = @@error
if ( @status <> 0 ) 
   begin
   rollback actEQUIPO_ASIGNADO
   return @status
   end 
   
   
select @cita = cita, @inv_viejo= num_inventario_viejo from EQUIPO_NUEVO where num_inventario = @num_inventario   

update CITA 
   set estado = 'A' 
 where cita = @cita 
   and inv_nuevo = @num_inventario 
   
select @status = @@error
if ( @status <> 0 ) 
   begin
   rollback actEQUIPO_ASIGNADO
   return @status
   end   
   
/*------------------------Se registra la recolección del equipo--------------------------------*/   


select @fecha_recoleccion = convert(char(10),getdate(),112)
exec bmgral..spbm_da_n_dias_antes_despues @fecha_recoleccion ,5,'S',@fecha_recoleccion output

   
select @id = isnull(max(id),0)+1 from RECOLECCION     --TODO ias habiles o naturales?

insert into RECOLECCION   
            ( id, fecha_original, fecha_actual,  fecha_cambio, clave_cambio, status ) 
	 values (@id,@fecha_recoleccion,@fecha_recoleccion,getdate(),@clave_usuario, 'A')
		
select @status = @@error
if ( @status <> 0 ) 
   begin
   rollback actEQUIPO_ASIGNADO
   return @status
   end   
   
insert into RECOLECCION_EQUIPO 
            ( id, num_inventario ,principal,recolectado) 
     values (@id,@inv_viejo ,'S','N') 
   
   
select @status = @@error
if ( @status <> 0 ) 
   begin
   rollback actEQUIPO_ASIGNADO
   return @status
   end   

insert into RECOLECCION_EQUIPO 
            ( id, num_inventario ,principal,recolectado) 
     select @id, num_inventario_hijo ,'N','N'
	   from ASOCIADO 
	  where num_inventario = @inv_viejo
  
select @status = @@error
if ( @status <> 0 ) 
   begin
   rollback actEQUIPO_ASIGNADO
   return @status
   end     

update EQUIPO_ASIGNADO 
   set edo_asignacion ='T' 
 where num_inventario = @inv_viejo
       
select @status = @@error
if ( @status <> 0 ) 
   begin
   rollback actEQUIPO_ASIGNADO
   return @status
   end   
   
 select @num_cambio = isnull(max(num_cambio),0)+1 from EQUIPO_ESTADO where num_inventario = @inv_viejo     
insert into EQUIPO_ESTADO  
            ( num_cambio, num_inventario, edo_asignacion, f_cambio, clave_cambio) 
	 values (@num_cambio,@inv_viejo,'T',getdate(),@clave_usuario)

select @status = @@error
if ( @status <> 0 ) 
   begin
   rollback actEQUIPO_ASIGNADO
   return @status
   end   
	 
commit  actEQUIPO_ASIGNADO




return 0 
go 
grant execute on spsae_usu_acepta_equipo to web_app
go

   