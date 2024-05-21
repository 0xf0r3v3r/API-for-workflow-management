from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models import Edge, NodeInterface, StartNode, MessageNode, ConditionNode, EdgeType, WorkFlow
from src.repositories.repository_base import BaseRepository


class EdgeRepository(BaseRepository):
    def __init__(self, session: AsyncSession):
        super().__init__(session=session, model=Edge)

    async def get_edge_start_node(self, query):
        result = await self._session.execute(query)
        edge_start_node = result.scalar_one_or_none()
        if not edge_start_node:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="The node from which the edge begins with the specified ID is not belong to specified workflow")
        return edge_start_node

    async def get_edge_end_node(self, query):
        result = await self._session.execute(query)
        edge_end_node = result.scalar_one_or_none()
        if not edge_end_node:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="The node where the edge ends with the specified ID is not belong to specified workflow")
        return edge_end_node

    async def validate_edge_type(self, edge_type, out_node):
        if edge_type != EdgeType.DEFAULT and out_node.discriminator != "conditionnode":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"Only Condition nodes can have {edge_type.value.upper()} edge type")

    async def validate_out_node(self, out_node):
        if not out_node:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The node from which the edge begins with the specified ID was not found")
        elif out_node.discriminator == "endnode":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="End node can't have output edges")

    async def validate_in_node(self, in_node):
        if not in_node:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The node where the edge ends with the specified ID was not found")
        elif in_node.discriminator == "startnode":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Start node can't have input edges")

    async def add(self, values: dict):
        query = select(WorkFlow).where(WorkFlow.id == values["workflow_id"])
        result = await self._session.execute(query)
        workflow = result.scalar_one_or_none()
        if not workflow:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Workflow not found")
        if values["start_node_id"] == values["end_node_id"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot create a self-loop (a node connected to itself)"
            )

        edge_type = values.get("edge_type")
        out_node_query = select(NodeInterface).where(NodeInterface.id == values["start_node_id"])
        result = await self._session.execute(out_node_query)
        out_node = result.scalar_one_or_none()

        await self.validate_edge_type(edge_type, out_node)
        await self.validate_out_node(out_node)

        if out_node.discriminator == "startnode":
            query = select(StartNode).where(StartNode.id == values["start_node_id"], StartNode.workflow_id == values["workflow_id"])
            edge_start_node = await self.get_edge_start_node(query=query)
            if edge_start_node.has_out_edge:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Out node (Start node) already has output edge"
                )
            edge_start_node.has_out_edge = True

        elif out_node.discriminator == "messagenode":
            query = select(MessageNode).where(MessageNode.id == values["start_node_id"], MessageNode.workflow_id == values["workflow_id"])
            edge_start_node = await self.get_edge_start_node(query=query)
            if edge_start_node.has_out_edge:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Out node (Message node) already has output edge"
                )
            edge_start_node.has_out_edge = True

        elif out_node.discriminator == "conditionnode":
            query = select(ConditionNode).where(ConditionNode.id == values["start_node_id"], ConditionNode.workflow_id == values["workflow_id"])
            edge_start_node = await self.get_edge_start_node(query=query)
            if edge_type == EdgeType.DEFAULT:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Out node (Condition node) can't have {edge_type.value.upper()} type edge"
                )
            if edge_type == EdgeType.YES and edge_start_node.yes_edge_count:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Out node (Condition node) already has {edge_type.value.upper()} edge"
                )
            if edge_type == EdgeType.YES:
                edge_start_node.yes_edge_count = True
            if edge_type == EdgeType.NO and edge_start_node.no_edge_count:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Out node (Condition node) already has {edge_type.value.upper()} edge"
                )
            if edge_type == EdgeType.NO:
                edge_start_node.no_edge_count = True

        else:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Discriminator error")

        in_node_query = select(NodeInterface).where(NodeInterface.id == values["end_node_id"])
        result = await self._session.execute(in_node_query)
        in_node = result.scalar_one_or_none()

        await self.validate_in_node(in_node)

        stmt = self.construct_add_stmt(values)
        result = await self._session.execute(stmt)
        await self._session.commit()
        return result.scalar_one()

    async def update_edge_counts(self, session: AsyncSession, target):
        start_node = target.start_node

        if start_node:
            if isinstance(start_node, MessageNode):
                await session.execute(
                    start_node.__table__.update()
                    .where(start_node.__table__.c.id == start_node.id)
                    .values(has_out_edge=False)
                )
            elif isinstance(start_node, StartNode):
                await session.execute(
                    start_node.__table__.update()
                    .where(start_node.__table__.c.id == start_node.id)
                    .values(has_out_edge=False)
                )

            elif isinstance(start_node, ConditionNode):
                if target.edge_type == EdgeType.YES:
                    await session.execute(
                        start_node.__table__.update()
                        .where(start_node.__table__.c.id == start_node.id)
                        .values(yes_edge_count=False)
                    )
                elif target.edge_type == EdgeType.NO:
                    await session.execute(
                        start_node.__table__.update()
                        .where(start_node.__table__.c.id == start_node.id)
                        .values(no_edge_count=False)
                    )

    async def delete(self, model_object_id: int):
        result = await self._session.execute(select(self._model).options(selectinload(self._model.start_node)).where(self._model.id == model_object_id))
        edge = result.scalar_one_or_none()
        if not edge:
            raise HTTPException(status_code=404, detail=f"{self._model.__name__} with the specified id was not found")
        await self._session.delete(edge)

        await self.update_edge_counts(session=self._session, target=edge)

        await self._session.commit()
